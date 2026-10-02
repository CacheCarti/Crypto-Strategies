from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class RsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "RsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 32400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 36
        self.pivot_radius = 2
        self.min_pivot_gap = 5
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = 0

    def _compute_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = []
        losses = []
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsi_values = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0.0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0.0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

        return rsi_values

    def _find_pivots(self, series: List[float], is_low: bool, radius: int = 2) -> List[Tuple[int, float]]:
        pivots = []
        n = len(series)
        for i in range(radius, n - radius):
            window = series[i - radius : i + radius + 1]
            if is_low and series[i] == min(window):
                pivots.append((i, series[i]))
            elif not is_low and series[i] == max(window):
                pivots.append((i, series[i]))
        return pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(total_bars_needed)
        highs = ctx.highs(self.lookback)
        lows = ctx.lows(self.lookback)

        if len(closes) < total_bars_needed or len(highs) < self.lookback or len(lows) < self.lookback:
            return None

        rsi_series = self._compute_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        recent_rsi = rsi_series[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        recent_highs = highs[-self.lookback:]
        recent_closes = closes[-self.lookback:]

        # Check existing position management
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if direction == "long":
                if current_rsi >= 58.0 or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_rsi_normalized_or_timeout",
                            "rsi": current_rsi,
                            "bars_held": bars_held,
                        },
                    )
            elif direction == "short":
                if current_rsi <= 42.0 or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_rsi_normalized_or_timeout",
                            "rsi": current_rsi,
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Bullish Divergence Detection
        # Price: Lower Low, RSI: Higher Low
        price_low_pivots = self._find_pivots(recent_lows, is_low=True, radius=self.pivot_radius)
        rsi_low_pivots = self._find_pivots(recent_rsi, is_low=True, radius=self.pivot_radius)

        if len(price_low_pivots) >= 2 and len(rsi_low_pivots) >= 2:
            p1_p, p2_p = price_low_pivots[-2], price_low_pivots[-1]
            p1_r, p2_r = rsi_low_pivots[-2], rsi_low_pivots[-1]

            if (p2_p[0] - p1_p[0] >= self.min_pivot_gap) and (p2_p[0] >= self.lookback - 6):
                # Price made lower low, RSI made higher low in oversold/rebound territory
                if p2_p[1] < p1_p[1] and p2_r[1] > p1_r[1] and p2_r[1] < 42.0:
                    if recent_closes[-1] > p2_p[1]:
                        conf = 0.70 if fear_greed < 45.0 else 0.60
                        self.entry_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=conf,
                            stop_loss_bps=260.0,
                            take_profit_bps=520.0,
                            horizon_seconds=32400,
                            metadata={
                                "reason": "rsi_bullish_divergence",
                                "price_p1": p1_p[1],
                                "price_p2": p2_p[1],
                                "rsi_p1": p1_r[1],
                                "rsi_p2": p2_r[1],
                                "rsi": current_rsi,
                                "fear_greed": fear_greed,
                            },
                        )

        # Bearish Divergence Detection
        # Price: Higher High, RSI: Lower High
        price_high_pivots = self._find_pivots(recent_highs, is_low=False, radius=self.pivot_radius)
        rsi_high_pivots = self._find_pivots(recent_rsi, is_low=False, radius=self.pivot_radius)

        if len(price_high_pivots) >= 2 and len(rsi_high_pivots) >= 2:
            p1_p, p2_p = price_high_pivots[-2], price_high_pivots[-1]
            p1_r, p2_r = rsi_high_pivots[-2], rsi_high_pivots[-1]

            if (p2_p[0] - p1_p[0] >= self.min_pivot_gap) and (p2_p[0] >= self.lookback - 6):
                # Price made higher high, RSI made lower high in overbought/exhaustion territory
                if p2_p[1] > p1_p[1] and p2_r[1] < p1_r[1] and p2_r[1] > 58.0:
                    if recent_closes[-1] < p2_p[1]:
                        conf = 0.70 if fear_greed > 55.0 else 0.60
                        self.entry_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=conf,
                            stop_loss_bps=260.0,
                            take_profit_bps=520.0,
                            horizon_seconds=32400,
                            metadata={
                                "reason": "rsi_bearish_divergence",
                                "price_p1": p1_p[1],
                                "price_p2": p2_p[1],
                                "rsi_p1": p1_r[1],
                                "rsi_p2": p2_r[1],
                                "rsi": current_rsi,
                                "fear_greed": fear_greed,
                            },
                        )

        return None