from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "ETH Panic Recovery Reversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.drop_pct_threshold = 0.048
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.midpoint_target = 0.0

    def _rsi(self, closes, period=12):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close

        # Position management: manual midpoint exit
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if self.midpoint_target > 0.0 and current_close >= self.midpoint_target:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "flush_midpoint_target_reached",
                            "price": current_close,
                            "midpoint_target": self.midpoint_target,
                            "flush_high": self.flush_high,
                            "flush_low": self.flush_low,
                        }
                    )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush analysis
        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        swing_high = max(recent_highs)
        swing_low = min(recent_lows)
        prev_high = highs[-2]

        drop_from_high = (swing_high - swing_low) / swing_high if swing_high > 0 else 0.0
        rsi = self._rsi(closes, period=12)
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Triggers: 4.8%+ flush, stabilization (close > prev_high), RSI not overbought
        is_panic_drop = drop_from_high >= self.drop_pct_threshold
        is_stabilized = current_close > prev_high
        rsi_valid = rsi < 52.0

        if is_panic_drop and is_stabilized and rsi_valid:
            self.flush_high = swing_high
            self.flush_low = swing_low
            self.midpoint_target = (swing_high + swing_low) / 2.0

            # Scale confidence based on severity of drop
            base_conf = 0.65
            drop_bonus = min(0.25, (drop_from_high - self.drop_pct_threshold) * 4.0)
            confidence = min(0.9, base_conf + drop_bonus)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_stabilization_long",
                    "drop_pct": round(drop_from_high * 100, 2),
                    "rsi": round(rsi, 2),
                    "swing_high": swing_high,
                    "swing_low": swing_low,
                    "midpoint_target": self.midpoint_target,
                    "prev_high": prev_high,
                    "close": current_close,
                    "fear_greed": fear_greed,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index