import math
from typing import Optional, Dict, Any, List, Tuple
from domains.strategy_contract import Strategy, BarContext, Signal

class RsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "RSI Divergence Swing",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 18
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.low_pivots: List[Tuple[int, float, float]] = []
        self.high_pivots: List[Tuple[int, float, float]] = []

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = 0.0
        losses = 0.0
        for i in range(1, period + 1):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period

        for i in range(period + 1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gain = diff if diff > 0 else 0.0
            loss = -diff if diff < 0 else 0.0
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)
        if len(closes) < 50:
            return None

        curr_rsi = self._rsi(closes, self.rsi_period)
        if curr_rsi is None:
            return None

        # Position Management & Normalization Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0

            if direction == "long":
                if curr_rsi >= 58.0 or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_rsi_normalized_or_time_exit",
                            "rsi": round(curr_rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            elif direction == "short":
                if curr_rsi <= 42.0 or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_rsi_normalized_or_time_exit",
                            "rsi": round(curr_rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Check entry cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Pivot Detection on bar [t - 3] with 2 confirmation bars (t-2, t-1)
        pivot_idx = ctx.bar_index - 3
        p_low = lows[-3]
        p_high = highs[-3]

        is_swing_low = (
            p_low < lows[-5] and p_low < lows[-4] and
            p_low <= lows[-2] and p_low <= lows[-1]
        )
        is_swing_high = (
            p_high > highs[-5] and p_high > highs[-4] and
            p_high >= highs[-2] and p_high >= highs[-1]
        )

        signal_to_fire = None

        if is_swing_low:
            rsi_at_pivot = self._rsi(closes[:-2], self.rsi_period)
            if rsi_at_pivot is not None:
                # Check for Bullish Divergence against previous swing low
                if self.low_pivots:
                    prev_idx, prev_low, prev_rsi = self.low_pivots[-1]
                    bars_diff = pivot_idx - prev_idx
                    if 5 <= bars_diff <= 35:
                        # Price lower low, RSI higher low, RSI oversold/recovering
                        if p_low < prev_low * 0.9985 and rsi_at_pivot > prev_rsi + 1.5 and rsi_at_pivot < 45.0:
                            signal_to_fire = ctx.signal(
                                "long",
                                confidence=0.75,
                                stop_loss_bps=250.0,
                                take_profit_bps=450.0,
                                metadata={
                                    "reason": "bullish_rsi_divergence",
                                    "curr_pivot_low": round(p_low, 2),
                                    "prev_pivot_low": round(prev_low, 2),
                                    "curr_pivot_rsi": round(rsi_at_pivot, 2),
                                    "prev_pivot_rsi": round(prev_rsi, 2),
                                    "rsi": round(curr_rsi, 2),
                                },
                            )
                self.low_pivots.append((pivot_idx, p_low, rsi_at_pivot))
                if len(self.low_pivots) > 8:
                    self.low_pivots.pop(0)

        if is_swing_high and signal_to_fire is None:
            rsi_at_pivot = self._rsi(closes[:-2], self.rsi_period)
            if rsi_at_pivot is not None:
                # Check for Bearish Divergence against previous swing high
                if self.high_pivots:
                    prev_idx, prev_high, prev_rsi = self.high_pivots[-1]
                    bars_diff = pivot_idx - prev_idx
                    if 5 <= bars_diff <= 35:
                        # Price higher high, RSI lower high, RSI overbought/cooling
                        if p_high > prev_high * 1.0015 and rsi_at_pivot < prev_rsi - 1.5 and rsi_at_pivot > 55.0:
                            signal_to_fire = ctx.signal(
                                "short",
                                confidence=0.75,
                                stop_loss_bps=250.0,
                                take_profit_bps=450.0,
                                metadata={
                                    "reason": "bearish_rsi_divergence",
                                    "curr_pivot_high": round(p_high, 2),
                                    "prev_pivot_high": round(prev_high, 2),
                                    "curr_pivot_rsi": round(rsi_at_pivot, 2),
                                    "prev_pivot_rsi": round(prev_rsi, 2),
                                    "rsi": round(curr_rsi, 2),
                                },
                            )
                self.high_pivots.append((pivot_idx, p_high, rsi_at_pivot))
                if len(self.high_pivots) > 8:
                    self.high_pivots.pop(0)

        if signal_to_fire is not None:
            self.entry_bar = ctx.bar_index
            return signal_to_fire

        return None