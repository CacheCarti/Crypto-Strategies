from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.flush_drop_threshold = 0.045  # 4.5% drop threshold
        self.cooldown_period = 8  # wait 8 bars after exit before entering again
        self.last_exit_bar = -999
        self.target_exit_price: Optional[float] = None
        self.entry_bar_index = 0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 2)
        highs = ctx.highs(self.lookback_bars + 2)
        lows = ctx.lows(self.lookback_bars + 2)

        if len(closes) < self.lookback_bars + 1 or len(highs) < self.lookback_bars + 1:
            return None

        current_price = ctx.bar.close
        current_bar = ctx.bar_index

        # Position Management
        if ctx.has_position():
            # If we hold a long position, check take profit target or time stop
            bars_held = current_bar - self.entry_bar_index
            if self.target_exit_price and current_price >= self.target_exit_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_rebound_target_reached",
                        "target_price": self.target_exit_price,
                        "current_price": current_price,
                        "bars_held": bars_held,
                    },
                )
            # Max hold safeguard (e.g. 14 bars / 14 hours for swing)
            if bars_held >= 14:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_exhaustion_exit",
                        "current_price": current_price,
                        "bars_held": bars_held,
                    },
                )
            return None

        # Cooldown guard after position close
        if current_bar - self.last_exit_bar < self.cooldown_period:
            return None

        # Avoid market crisis meltdowns
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # 1. Identify Flush: highest high in the last 24 bars vs recent low
        recent_highs = highs[-self.lookback_bars:]
        recent_lows = lows[-self.lookback_bars:]
        flush_high = max(recent_highs)
        flush_low = min(recent_lows)

        if flush_high <= 0:
            return None

        drawdown = (flush_high - ctx.bar.low) / flush_high

        # 2. Reversal / Stabilization signal:
        # Bar closes above previous bar's high, confirming buying response
        prev_high = highs[-2]
        is_stabilizing = (current_price > prev_high) and (closes[-1] > closes[-2])

        # 3. Oversold context confirmation: RSI < 42 confirms prior dislocation
        rsi_val = self._rsi(closes, 14)
        rsi_oversold = rsi_val is not None and rsi_val < 42.0

        if drawdown >= self.flush_drop_threshold and is_stabilizing and rsi_oversold:
            # Rebound target: midpoint between flush_low and flush_high
            midpoint = flush_low + 0.50 * (flush_high - flush_low)
            # Ensure target provides viable upside (> 70 bps above current price)
            if midpoint <= current_price * 1.007:
                return None

            self.target_exit_price = midpoint
            self.entry_bar_index = current_bar

            # Calculate bps to midpoint for dynamic take profit
            bps_to_mid = ((midpoint - current_price) / current_price) * 10000.0
            tp_bps = min(max(bps_to_mid, 180.0), 450.0)
            sl_bps = 260.0

            fear_greed = ctx.features.get("fear_greed_index", 50)
            confidence = 0.85 if fear_greed < 40 else 0.70

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_reversal_stabilization",
                    "drawdown_pct": round(drawdown * 100, 2),
                    "rsi": round(rsi_val, 2) if rsi_val else 0.0,
                    "target_midpoint": round(midpoint, 2),
                    "prev_high": round(prev_high, 2),
                    "fear_greed": fear_greed,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_exit_price = None