from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 21600,  # 6 hours expected recovery
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = 0.045         # 4.5% drop over 24h window
        self.cooldown_period = 18        # Bars to wait after exit or entry
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        prev_high = highs[-2]
        prev_close = closes[-2]

        # Manage existing long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Midpoint take profit or hold timeout
            if current_close >= self.target_midpoint and self.target_midpoint > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reached",
                        "close": current_close,
                        "target_midpoint": self.target_midpoint,
                        "bars_held": bars_held
                    }
                )
            if bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_timeout",
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Compute drop over the flush window
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        if window_high <= 0:
            return None

        drop_pct = (window_high - prev_close) / window_high

        # Event boundary:
        # 1) Deep flush: drop >= 4.5% over the rolling window
        # 2) Stabilization pivot: current bar closes cleanly above previous bar's high
        # 3) Oversold relief indicator: RSI < 42 ensures we are buying after genuine exhaustion
        # 4) Not a meltdown/crisis regime to avoid catching falling knives in liquidation cascades
        rsi_val = self._rsi(closes, period=14)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")

        is_flush = drop_pct >= self.drop_thresh
        is_stabilizing = (current_close > prev_high) and (current_close > ctx.bar.open)
        is_exhausted = rsi_val < 42.0
        safe_regime = regime != "MELTDOWN"

        if is_flush and is_stabilizing and is_exhausted and safe_regime:
            self.flush_high = window_high
            self.flush_low = min(window_low, ctx.bar.low)
            self.target_midpoint = (self.flush_high + self.flush_low) / 2.0

            # Calculate dynamic distance to midpoint
            dist_to_mid_bps = ((self.target_midpoint - current_close) / current_close) * 10000.0

            # Only trade if reward to midpoint covers friction comfortably
            if dist_to_mid_bps >= 120.0:
                self.entry_bar = ctx.bar_index
                tp_bps = min(max(dist_to_mid_bps, 180.0), 450.0)
                sl_bps = 280.0
                confidence = min(0.65 + (drop_pct * 2.0), 0.95)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "panic_flush_stabilization_reversal",
                        "drop_pct": round(drop_pct * 100, 2),
                        "rsi": round(rsi_val, 2),
                        "window_high": round(window_high, 2),
                        "window_low": round(window_low, 2),
                        "target_midpoint": round(self.target_midpoint, 2),
                        "fear_greed": fear_greed
                    }
                )

        return None