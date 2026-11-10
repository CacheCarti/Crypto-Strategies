from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        if len(gains) < period:
            return []

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsi_series = []
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

    def _find_pivots(self, series: list, window: int = 3):
        low_pivots = []
        high_pivots = []
        for i in range(window, len(series) - window):
            val = series[i]
            is_low = all(val <= series[i - j] and val <= series[i + j] for j in range(1, window + 1))
            is_high = all(val >= series[i - j] and val >= series[i + j] for j in range(1, window + 1))
            if is_low:
                low_pivots.append((i, val))
            if is_high:
                high_pivots.append((i, val))
        return low_pivots, high_pivots

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        bars_since_last = ctx.bar_index - self.last_trade_bar

        # Check existing position for exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion normalization exit
            if pos_dir == "long" and current_rsi >= 55.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_long", "rsi": current_rsi})
            elif pos_dir == "short" and current_rsi <= 45.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_short", "rsi": current_rsi})
            return None

        # Mandatory cooldown
        if bars_since_last < self.cooldown_bars:
            return None

        # Align closes to rsi series
        aligned_closes = closes[-len(rsi_series):]
        rsi_window = rsi_series[-self.lookback:]
        price_window = aligned_closes[-self.lookback:]

        # Detect pivots using local window of 2 bars on each side
        price_lows, price_highs = self._find_pivots(price_window, window=2)
        rsi_lows, rsi_highs = self._find_pivots(rsi_window, window=2)

        # Bullish divergence check: Price Lower Low while RSI Higher Low
        if len(price_lows) >= 2 and len(rsi_lows) >= 2:
            p_prev, p_curr = price_lows[-2], price_lows[-1]
            r_prev, r_curr = rsi_lows[-2], rsi_lows[-1]

            # Matching temporal order and separation
            if p_curr[0] > p_prev[0] and r_curr[0] > r_prev[0] and (p_curr[0] - p_prev[0]) >= 4:
                # Lower low in price, higher low in RSI, and in oversold/rebound zone
                if p_curr[1] < p_prev[1] and r_curr[1] > r_prev[1] and current_rsi < 42.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=280.0,
                        take_profit_bps=480.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "p_prev": p_prev[1],
                            "p_curr": p_curr[1],
                            "r_prev": r_prev[1],
                            "r_curr": r_curr[1]
                        }
                    )

        # Bearish divergence check: Price Higher High while RSI Lower High
        if len(price_highs) >= 2 and len(rsi_highs) >= 2:
            p_prev, p_curr = price_highs[-2], price_highs[-1]
            r_prev, r_curr = rsi_highs[-2], rsi_highs[-1]

            # Matching temporal order and separation
            if p_curr[0] > p_prev[0] and r_curr[0] > r_prev[0] and (p_curr[0] - p_prev[0]) >= 4:
                # Higher high in price, lower high in RSI, and in overbought/exhaustion zone
                if p_curr[1] > p_prev[1] and r_curr[1] < r_prev[1] and current_rsi > 58.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=280.0,
                        take_profit_bps=480.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "p_prev": p_prev[1],
                            "p_curr": p_curr[1],
                            "r_prev": r_prev[1],
                            "r_curr": r_curr[1]
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index