from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # 6 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.045       # 4.5% drop over 24h window (robust crash filter)
        self.cooldown_bars = 12         # Mandatory post-trade or post-signal cooldown
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.midpoint_target = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
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
        self.last_trade_bar = ctx.bar_index
        self.midpoint_target = 0.0
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_idx = ctx.bar_index
        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            # Exit at 24-bar drop midpoint or after max 8 bars to prevent stagnation
            if current_price >= self.midpoint_target and self.midpoint_target > 0.0:
                self.last_trade_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "midpoint_rebound_target_reached",
                        "price": current_price,
                        "target": self.midpoint_target,
                        "bars_held": bars_held,
                    }
                )
            if bars_held >= 8:
                self.last_trade_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_recovery_horizon_exit",
                        "price": current_price,
                        "bars_held": bars_held,
                    }
                )
            return None

        # Cooldown enforcement
        if current_idx - self.last_trade_bar < self.cooldown_bars:
            return None

        # 24-bar window drop calculation
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        prev_close = closes[-2]
        prev_high = highs[-2]

        drop_from_high = (window_high - window_low) / window_high

        # Crash criteria: drop of at least 4.5% in 24 bars
        if drop_from_high < self.min_drop_pct:
            return None

        # Capitulation reversal trigger: current close breaks back above previous bar's high
        reversal_candle = current_price > prev_high and current_price > prev_close

        # Complementary oversold check to confirm true liquidation flush
        rsi = self._rsi(closes, period=14)
        if rsi is None or rsi > 42.0:
            return None

        if reversal_candle:
            self.midpoint_target = (window_high + window_low) / 2.0
            self.entry_bar = current_idx
            self.last_trade_bar = current_idx

            # Confidence scaled by depth of crash
            conf = min(0.9, max(0.55, 0.5 + (drop_from_high * 4.0)))

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "capitulation_reversal_flush_bounce",
                    "drop_pct": round(drop_from_high * 100, 2),
                    "rsi": round(rsi, 2),
                    "window_high": round(window_high, 2),
                    "window_low": round(window_low, 2),
                    "midpoint_target": round(self.midpoint_target, 2),
                    "entry_price": current_price,
                }
            )

        return None