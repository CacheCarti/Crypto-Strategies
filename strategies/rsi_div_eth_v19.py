from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.bars_in_position = 0

    def _rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        rsis = []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rs = 100.0 if avg_loss == 0 else avg_gain / avg_loss
        rsis.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100.0 - (100.0 / (1.0 + rs)))
        return rsis

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)

        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsis = self._rsi_series(closes, self.rsi_period)
        if len(rsis) < self.lookback:
            return None

        curr_rsi = rsis[-1]
        curr_price = closes[-1]

        if ctx.has_position():
            self.bars_in_position += 1
            direction = ctx.position_direction()
            
            # Dynamic normalization exit or time-stop (approx 12-16 hours)
            if direction == "long" and (curr_rsi >= 55.0 or self.bars_in_position >= 14):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_rsi_normalized", "rsi": curr_rsi})
            elif direction == "short" and (curr_rsi <= 45.0 or self.bars_in_position >= 14):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_rsi_normalized", "rsi": curr_rsi})
            return None

        self.bars_in_position = 0
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Detect pivots across recent lookback
        # Window of interest: past 5 to 25 bars compared to current / recent bars
        recent_lows = lows[-self.lookback:]
        recent_highs = highs[-self.lookback:]
        recent_rsis = rsis[-self.lookback:]

        # Find prior swing low (bar 5 to 25 in the past)
        pivot_low_idx = -1
        min_p_val = float("inf")
        for i in range(5, len(recent_lows) - 3):
            if recent_lows[i] == min(recent_lows[max(0, i - 3):min(len(recent_lows), i + 4)]):
                if recent_lows[i] < min_p_val:
                    min_p_val = recent_lows[i]
                    pivot_low_idx = i

        # Find prior swing high (bar 5 to 25 in the past)
        pivot_high_idx = -1
        max_p_val = float("-inf")
        for i in range(5, len(recent_highs) - 3):
            if recent_highs[i] == max(recent_highs[max(0, i - 3):min(len(recent_highs), i + 4)]):
                if recent_highs[i] > max_p_val:
                    max_p_val = recent_highs[i]
                    pivot_high_idx = i

        # Bullish divergence: price makes lower low, RSI makes higher low
        # Current bar or recent 2 bars at lowest price, but RSI strictly higher than pivot RSI
        if pivot_low_idx != -1:
            p_rsi = recent_rsis[pivot_low_idx]
            if p_rsi < 38.0 and recent_lows[-1] < min_p_val and curr_rsi > p_rsi + 3.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=280.0,
                    take_profit_bps=450.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "bullish_rsi_divergence",
                        "rsi": curr_rsi,
                        "pivot_rsi": p_rsi,
                        "price": curr_price,
                        "pivot_price": min_p_val
                    }
                )

        # Bearish divergence: price makes higher high, RSI makes lower high
        if pivot_high_idx != -1:
            p_rsi = recent_rsis[pivot_high_idx]
            if p_rsi > 62.0 and recent_highs[-1] > max_p_val and curr_rsi < p_rsi - 3.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=280.0,
                    take_profit_bps=450.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "bearish_rsi_divergence",
                        "rsi": curr_rsi,
                        "pivot_rsi": p_rsi,
                        "price": curr_price,
                        "pivot_price": max_p_val
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.bars_in_position = 0