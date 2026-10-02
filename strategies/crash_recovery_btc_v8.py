from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicRecovery(Strategy):
    METADATA = {
        "name": "BTC Panic Flush Recovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.025
        self.cooldown_bars = 4
        self.max_hold_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.entry_midpoint = 0.0

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1
        self.entry_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = closes[-1]
        current_open = ctx.bar.open
        prev_high = highs[-2]
        rsi = self._rsi(closes, 14) or 50.0

        # Position management
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

            if self.entry_midpoint > 0.0 and current_close >= self.entry_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "close": current_close,
                        "entry_midpoint": self.entry_midpoint,
                        "bars_held": bars_in_pos,
                        "rsi": rsi,
                    },
                )

            if rsi >= 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "rsi_rebound_overbought_exit",
                        "close": current_close,
                        "rsi": rsi,
                        "bars_held": bars_in_pos,
                    },
                )

            if bars_in_pos >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.60,
                    metadata={
                        "reason": "max_hold_time_reached",
                        "close": current_close,
                        "bars_held": bars_in_pos,
                    },
                )

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        high_24 = max(recent_highs)
        low_24 = min(recent_lows)

        if high_24 <= 0.0:
            return None

        # Measure 24-bar drop amplitude
        drop_pct = (high_24 - low_24) / high_24

        if drop_pct >= self.min_drop_pct:
            # Reversal confirmation: close above prior high or strong bullish engulfing bar
            is_reversal = (current_close > prev_high) or (current_close > current_open and current_close > (highs[-2] + lows[-2]) / 2.0)
            midpoint = (high_24 + low_24) / 2.0

            # Price still has upside to reach the flush midpoint
            if is_reversal and current_close <= midpoint:
                self.entry_bar = ctx.bar_index
                self.entry_midpoint = midpoint
                confidence = min(0.85, max(0.55, 0.50 + (drop_pct * 3.0)))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "panic_flush_reversal_entry",
                        "drop_pct": round(drop_pct * 100, 2),
                        "high_24": high_24,
                        "low_24": low_24,
                        "midpoint": midpoint,
                        "rsi": round(rsi, 2),
                        "close": current_close,
                    },
                )

        return None