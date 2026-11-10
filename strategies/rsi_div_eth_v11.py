from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 4
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        series = []
        if avg_loss == 0.0:
            series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            series.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                series.append(100.0 - (100.0 / (1.0 + rs)))
        return series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.lookback + self.rsi_period + 5
        closes = ctx.closes(req_bars)
        lows = ctx.lows(req_bars)
        highs = ctx.highs(req_bars)

        if len(closes) < req_bars:
            return None

        rsi_vals = self._rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback + 3:
            return None

        current_rsi = rsi_vals[-1]
        current_close = closes[-1]
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Check Active Position Exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            pos_dir = ctx.position_direction()

            # Normalization exit or max hold time stop
            if pos_dir == "long":
                if current_rsi >= 58.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_rsi_normalized_or_time", "rsi": current_rsi, "bars_held": bars_held})
            elif pos_dir == "short":
                if current_rsi <= 42.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_rsi_normalized_or_time", "rsi": current_rsi, "bars_held": bars_held})
            return None

        # Cooldown guard
        if bars_since_exit < self.cooldown_bars:
            return None

        # Pivot Detection over lookback window (excluding current bar)
        # Using 3-bar local extrema: x[i-1] > x[i] < x[i+1]
        r_slice = rsi_vals[-self.lookback:]
        l_slice = lows[-self.lookback:]
        h_slice = highs[-self.lookback:]

        # Search for pivots
        low_pivots = []
        for i in range(1, len(r_slice) - 2):
            if l_slice[i] < l_slice[i - 1] and l_slice[i] < l_slice[i + 1]:
                low_pivots.append((i, l_slice[i], r_slice[i]))

        high_pivots = []
        for i in range(1, len(r_slice) - 2):
            if h_slice[i] > h_slice[i - 1] and h_slice[i] > h_slice[i + 1]:
                high_pivots.append((i, h_slice[i], r_slice[i]))

        # Bullish Divergence: Current Low is lower than previous trough, but RSI is higher
        if len(low_pivots) >= 1 and current_rsi < 38.0:
            prev_idx, prev_low, prev_rsi = low_pivots[-1]
            # Ensure proper separation
            if (len(r_slice) - 1 - prev_idx) >= 3:
                curr_low = min(lows[-3:])
                if curr_low < prev_low and current_rsi > prev_rsi:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=280.0,
                        take_profit_bps=420.0,
                        horizon_seconds=14400,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "prev_rsi": prev_rsi,
                            "price": current_close,
                            "prev_low": prev_low,
                        }
                    )

        # Bearish Divergence: Current High is higher than previous peak, but RSI is lower
        if len(high_pivots) >= 1 and current_rsi > 62.0:
            prev_idx, prev_high, prev_rsi = high_pivots[-1]
            if (len(r_slice) - 1 - prev_idx) >= 3:
                curr_high = max(highs[-3:])
                if curr_high > prev_high and current_rsi < prev_rsi:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=280.0,
                        take_profit_bps=420.0,
                        horizon_seconds=14400,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "prev_rsi": prev_rsi,
                            "price": current_close,
                            "prev_high": prev_high,
                        }
                    )

        return None