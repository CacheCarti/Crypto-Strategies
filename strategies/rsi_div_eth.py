from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class RsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "RsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback_bars = 36
        self.pivot_radius = 2
        self.cooldown_bars = 6
        self.max_hold_bars = 20
        self.last_exit_bar = -100
        self.bars_held = 0

    def _calculate_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_series = [50.0] * period
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        if avg_loss == 0.0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
            
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def _find_pivot_lows(self, lows: List[float], rsi_vals: List[float], radius: int = 2) -> List[Tuple[int, float, float]]:
        pivots = []
        n = len(lows)
        for i in range(radius, n - radius):
            curr_low = lows[i]
            is_pivot = True
            for d in range(1, radius + 1):
                if lows[i - d] <= curr_low or lows[i + d] < curr_low:
                    is_pivot = False
                    break
            if is_pivot:
                pivots.append((i, curr_low, rsi_vals[i]))
        return pivots

    def _find_pivot_highs(self, highs: List[float], rsi_vals: List[float], radius: int = 2) -> List[Tuple[int, float, float]]:
        pivots = []
        n = len(highs)
        for i in range(radius, n - radius):
            curr_high = highs[i]
            is_pivot = True
            for d in range(1, radius + 1):
                if highs[i - d] >= curr_high or highs[i + d] > curr_high:
                    is_pivot = False
                    break
            if is_pivot:
                pivots.append((i, curr_high, rsi_vals[i]))
        return pivots

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n_bars = self.lookback_bars + self.rsi_period + 5
        closes = ctx.closes(n_bars)
        highs = ctx.highs(n_bars)
        lows = ctx.lows(n_bars)

        if len(closes) < n_bars:
            return None

        rsi_series = self._calculate_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < len(closes):
            return None

        current_rsi = rsi_series[-1]
        current_close = ctx.bar.close

        # Position Management
        if ctx.has_position():
            self.bars_held += 1
            direction = ctx.position_direction()

            # Exit on RSI normalization or timeout
            if direction == "long":
                if current_rsi >= 62.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "rsi_overbought_exit",
                        "rsi": round(current_rsi, 2),
                        "bars_held": self.bars_held,
                        "close": current_close
                    })
                if self.bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "time_stop_exit",
                        "rsi": round(current_rsi, 2),
                        "bars_held": self.bars_held,
                        "close": current_close
                    })

            elif direction == "short":
                if current_rsi <= 38.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "rsi_oversold_exit",
                        "rsi": round(current_rsi, 2),
                        "bars_held": self.bars_held,
                        "close": current_close
                    })
                if self.bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "time_stop_exit",
                        "rsi": round(current_rsi, 2),
                        "bars_held": self.bars_held,
                        "close": current_close
                    })
            return None

        # Reset holding counter when flat
        self.bars_held = 0

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Analyze recent window for pivots
        window_len = self.lookback_bars
        sub_highs = highs[-window_len:]
        sub_lows = lows[-window_len:]
        sub_rsi = rsi_series[-window_len:]

        pivot_lows = self._find_pivot_lows(sub_lows, sub_rsi, self.pivot_radius)
        pivot_highs = self._find_pivot_highs(sub_highs, sub_rsi, self.pivot_radius)

        # Bullish Divergence: Price lower low, RSI higher low
        if len(pivot_lows) >= 2:
            p1_idx, p1_price, p1_rsi = pivot_lows[-2]
            p2_idx, p2_price, p2_rsi = pivot_lows[-1]

            # Require recent pivot to be fresh (within last 6 bars) and reasonably spaced
            if (window_len - 1 - p2_idx) <= 5 and (p2_idx - p1_idx) >= 5:
                if p2_price < p1_price and p2_rsi > (p1_rsi + 1.5) and current_rsi < 48.0:
                    confidence = min(0.9, 0.6 + (p2_rsi - p1_rsi) * 0.02)
                    return ctx.signal("long", confidence=confidence, metadata={
                        "reason": "bullish_rsi_divergence",
                        "rsi": round(current_rsi, 2),
                        "p1_price": round(p1_price, 2),
                        "p2_price": round(p2_price, 2),
                        "p1_rsi": round(p1_rsi, 2),
                        "p2_rsi": round(p2_rsi, 2),
                        "close": current_close
                    })

        # Bearish Divergence: Price higher high, RSI lower high
        if len(pivot_highs) >= 2:
            p1_idx, p1_price, p1_rsi = pivot_highs[-2]
            p2_idx, p2_price, p2_rsi = pivot_highs[-1]

            # Require recent pivot to be fresh (within last 6 bars) and reasonably spaced
            if (window_len - 1 - p2_idx) <= 5 and (p2_idx - p1_idx) >= 5:
                if p2_price > p1_price and p2_rsi < (p1_rsi - 1.5) and current_rsi > 52.0:
                    confidence = min(0.9, 0.6 + (p1_rsi - p2_rsi) * 0.02)
                    return ctx.signal("short", confidence=confidence, metadata={
                        "reason": "bearish_rsi_divergence",
                        "rsi": round(current_rsi, 2),
                        "p1_price": round(p1_price, 2),
                        "p2_price": round(p2_price, 2),
                        "p1_rsi": round(p1_rsi, 2),
                        "p2_rsi": round(p2_rsi, 2),
                        "close": current_close
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_held = 0