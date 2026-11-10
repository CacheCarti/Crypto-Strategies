from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class RobustRsiDivergence(Strategy):
    METADATA = {
        "name": "RobustRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 35
        self.pivot_window = 3
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _calc_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_series = []
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

    def _find_pivots(self, series: List[float], w: int = 3) -> Tuple[List[int], List[int]]:
        low_pivots = []
        high_pivots = []
        n = len(series)
        for i in range(w, n - w):
            val = series[i]
            if all(val < series[i - k] for k in range(1, w + 1)) and all(val <= series[i + k] for k in range(1, w + 1)):
                low_pivots.append(i)
            if all(val > series[i - k] for k in range(1, w + 1)) and all(val >= series[i + k] for k in range(1, w + 1)):
                high_pivots.append(i)
        return low_pivots, high_pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback + self.rsi_period + 10
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        curr_rsi = rsi_series[-1]
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_rsi >= 58.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_long", "rsi": curr_rsi})
            elif pos_dir == "short" and curr_rsi <= 42.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_short", "rsi": curr_rsi})
            return None

        if bars_since_exit < self.cooldown_bars:
            return None

        recent_closes = closes[-self.lookback:]
        recent_rsi = rsi_series[-self.lookback:]

        low_pivots, high_pivots = self._find_pivots(recent_closes, self.pivot_window)

        # Bullish Divergence: Price lower low, RSI higher low, recent pivot, current RSI oversold/recovering
        if len(low_pivots) >= 2:
            p1, p2 = low_pivots[-2], low_pivots[-1]
            dist_from_latest_pivot = (len(recent_closes) - 1) - p2
            if dist_from_latest_pivot <= 4 and (p2 - p1) >= 4:
                price_ll = recent_closes[p2] < recent_closes[p1] * 0.998
                rsi_hl = recent_rsi[p2] > recent_rsi[p1] + 1.5
                if price_ll and rsi_hl and recent_rsi[p2] < 42.0 and curr_rsi > recent_rsi[p2]:
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_div_confirmed",
                            "rsi": curr_rsi,
                            "p1_price": recent_closes[p1],
                            "p2_price": recent_closes[p2],
                            "p1_rsi": recent_rsi[p1],
                            "p2_rsi": recent_rsi[p2],
                        }
                    )

        # Bearish Divergence: Price higher high, RSI lower high, recent pivot, current RSI overbought/turning
        if len(high_pivots) >= 2:
            p1, p2 = high_pivots[-2], high_pivots[-1]
            dist_from_latest_pivot = (len(recent_closes) - 1) - p2
            if dist_from_latest_pivot <= 4 and (p2 - p1) >= 4:
                price_hh = recent_closes[p2] > recent_closes[p1] * 1.002
                rsi_lh = recent_rsi[p2] < recent_rsi[p1] - 1.5
                if price_hh and rsi_lh and recent_rsi[p2] > 58.0 and curr_rsi < recent_rsi[p2]:
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_div_confirmed",
                            "rsi": curr_rsi,
                            "p1_price": recent_closes[p1],
                            "p2_price": recent_closes[p2],
                            "p1_rsi": recent_rsi[p1],
                            "p2_rsi": recent_rsi[p2],
                        }
                    )

        return None