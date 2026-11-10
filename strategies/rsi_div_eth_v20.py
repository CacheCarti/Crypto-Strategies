from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List

class EthRsiDivergenceEvent(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceEvent",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours (5 x 1h bars)
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.bars_since_exit = 10
        self.entry_bar = -1
        self.max_hold_bars = 8

    def _calc_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = []
        losses = []
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsi_series = []
        # Initial averages
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0.0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        # Wilder's smoothing
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        
        # Warmup guard
        needed_bars = self.lookback + self.rsi_period + 5
        closes = ctx.closes(needed_bars)
        lows = ctx.lows(needed_bars)
        highs = ctx.highs(needed_bars)
        if len(closes) < needed_bars:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            
            # Compute current RSI
            rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
            if not rsi_vals:
                return None
            curr_rsi = rsi_vals[-1]

            # Normalization exit or max hold time
            if pos_dir == "long":
                if curr_rsi >= 55.0:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_long", "rsi": curr_rsi})
                if bars_held >= self.max_hold_bars:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_stop_long", "bars_held": bars_held})
            elif pos_dir == "short":
                if curr_rsi <= 45.0:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_short", "rsi": curr_rsi})
                if bars_held >= self.max_hold_bars:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_stop_short", "bars_held": bars_held})
            return None

        # Mandatory cooldown check
        if self.bars_since_exit < self.cooldown_bars:
            return None

        # Compute RSI history aligned with price bars
        rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback:
            return None

        # Slice to recent window
        w_lows = lows[-self.lookback:]
        w_highs = highs[-self.lookback:]
        w_rsi = rsi_vals[-self.lookback:]
        
        curr_price_low = w_lows[-1]
        curr_price_high = w_highs[-1]
        curr_rsi = w_rsi[-1]

        # Find historical pivot low / high in [0, lookback - 4]
        # Leaving a 3-bar buffer prevents comparing with immediate noise
        search_window = self.lookback - 4

        # 1. Bullish Divergence Event:
        # Strict boundary: Current RSI must be turning up from oversold/depressed region (< 38)
        # Price hits new local low, but RSI remains firmly higher than the previous low's RSI.
        min_p_idx = 0
        min_p_val = w_lows[0]
        for i in range(1, search_window):
            if w_lows[i] < min_p_val:
                min_p_val = w_lows[i]
                min_p_idx = i

        prev_rsi_at_low = w_rsi[min_p_idx]
        
        # Bullish setup:
        # - Current low breaks prior pivot low
        # - Current RSI strictly higher by at least 3.0 points than RSI at prior price low
        # - Trigger condition: RSI hook (curr_rsi > prev_bar_rsi) and oversold territory
        if (curr_price_low < min_p_val and 
            curr_rsi > prev_rsi_at_low + 3.0 and 
            curr_rsi < 38.0 and 
            curr_rsi > w_rsi[-2]):
            
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bullish_rsi_div_event",
                    "price_low": curr_price_low,
                    "prev_price_low": min_p_val,
                    "curr_rsi": curr_rsi,
                    "prev_rsi": prev_rsi_at_low,
                }
            )

        # 2. Bearish Divergence Event:
        # Strict boundary: Current RSI turning down from overbought territory (> 62)
        # Price hits new local high, but RSI remains lower than the previous high's RSI.
        max_p_idx = 0
        max_p_val = w_highs[0]
        for i in range(1, search_window):
            if w_highs[i] > max_p_val:
                max_p_val = w_highs[i]
                max_p_idx = i

        prev_rsi_at_high = w_rsi[max_p_idx]

        if (curr_price_high > max_p_val and 
            curr_rsi < prev_rsi_at_high - 3.0 and 
            curr_rsi > 62.0 and 
            curr_rsi < w_rsi[-2]):
            
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bearish_rsi_div_event",
                    "price_high": curr_price_high,
                    "prev_price_high": max_p_val,
                    "curr_rsi": curr_rsi,
                    "prev_rsi": prev_rsi_at_high,
                }
            )

        return None