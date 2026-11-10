from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 32
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 14

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[list]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_series = []
        if len(gains) < period:
            return None
            
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rs = avg_gain / avg_loss if avg_loss != 0 else 100.0
        rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            rs = avg_gain / avg_loss if avg_loss != 0 else 100.0
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period + 5:
            return None

        rsi_series = self._calc_rsi(closes, self.rsi_period)
        if not rsi_series or len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        current_close = closes[-1]

        # Manage active positions (exit when normalized or timed out)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Normalization exits or time stops
            if pos_dir == "long":
                if current_rsi >= 52.0 or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_rsi_normalized" if current_rsi >= 52.0 else "time_stop",
                            "rsi": round(current_rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            elif pos_dir == "short":
                if current_rsi <= 48.0 or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_rsi_normalized" if current_rsi <= 48.0 else "time_stop",
                            "rsi": round(current_rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Detect swing pivots across historical window
        window_closes = closes[-self.lookback:]
        window_rsi = rsi_series[-self.lookback:]

        low_pivots = []
        high_pivots = []

        for i in range(2, self.lookback - 2):
            if (window_closes[i] < window_closes[i - 1] and 
                window_closes[i] < window_closes[i - 2] and 
                window_closes[i] <= window_closes[i + 1] and 
                window_closes[i] <= window_closes[i + 2]):
                low_pivots.append((i, window_closes[i], window_rsi[i]))

            if (window_closes[i] > window_closes[i - 1] and 
                window_closes[i] > window_closes[i - 2] and 
                window_closes[i] >= window_closes[i + 1] and 
                window_closes[i] >= window_closes[i + 2]):
                high_pivots.append((i, window_closes[i], window_rsi[i]))

        # Bullish Divergence Setup: Lower Low in price with Higher Low in RSI
        if len(low_pivots) >= 2:
            prev_low = low_pivots[-2]
            curr_low = low_pivots[-1]
            if (curr_low[0] - prev_low[0] >= 5 and
                curr_low[0] >= self.lookback - 5 and
                curr_low[1] < prev_low[1] and 
                curr_low[2] > prev_low[2] and 
                current_rsi < 45.0 and
                current_close > curr_low[1]):
                
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.72,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_rsi_divergence",
                        "rsi": round(current_rsi, 2),
                        "prev_pivot_rsi": round(prev_low[2], 2),
                        "curr_pivot_rsi": round(curr_low[2], 2),
                        "prev_pivot_price": round(prev_low[1], 2),
                        "curr_pivot_price": round(curr_low[1], 2),
                    },
                )

        # Bearish Divergence Setup: Higher High in price with Lower High in RSI
        if len(high_pivots) >= 2:
            prev_high = high_pivots[-2]
            curr_high = high_pivots[-1]
            if (curr_high[0] - prev_high[0] >= 5 and
                curr_high[0] >= self.lookback - 5 and
                curr_high[1] > prev_high[1] and 
                curr_high[2] < prev_high[2] and 
                current_rsi > 55.0 and
                current_close < curr_high[1]):

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.72,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_rsi_divergence",
                        "rsi": round(current_rsi, 2),
                        "prev_pivot_rsi": round(prev_high[2], 2),
                        "curr_pivot_rsi": round(curr_high[2], 2),
                        "prev_pivot_price": round(prev_high[1], 2),
                        "curr_pivot_price": round(curr_high[1], 2),
                    },
                )

        return None