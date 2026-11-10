from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRangeBreak(Strategy):
    METADATA = {
        "name": "Volatility Range Break",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_vol = 20  # Period for volatility calculation (ATR)
        self.period_range = 30  # Period for identifying range
        self.range_multiplier = 1.5  # How many ATRs define the range
        self.cooldown_bars = 10  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period_range + self.period_vol)
        highs = ctx.highs(self.period_range + self.period_vol)
        lows = ctx.lows(self.period_range + self.period_vol)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate ATR for volatility
        atr = self._atr(highs, lows, closes, self.period_vol)
        if atr is None:
            return None

        # Define the recent range using SMA and ATR
        recent_closes = closes[-self.period_range:]
        if len(recent_closes) < self.period_range:
            return None
        
        mean_price = self._sma(recent_closes, self.period_range)
        if mean_price is None:
            return None

        upper_bound = mean_price + self.range_multiplier * atr
        lower_bound = mean_price - self.range_multiplier * atr

        # Check for failed range breakout (long entry)
        # Price broke below the lower bound, then closed back inside
        if not ctx.has_position() and ctx.bar.close > lower_bound and ctx.lows(2)[0] < lower_bound:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "failed_bearish_range_breakout",
                    "current_price": current_price,
                    "lower_bound": lower_bound,
                    "mean_price": mean_price,
                    "atr": atr,
                    "regime": ctx.regime,
                }
            )

        # Check for failed range breakout (short entry)
        # Price broke above the upper bound, then closed back inside
        if not ctx.has_position() and ctx.bar.close < upper_bound and ctx.highs(2)[0] > upper_bound:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "failed_bullish_range_breakout",
                    "current_price": current_price,
                    "upper_bound": upper_bound,
                    "mean_price": mean_price,
                    "atr": atr,
                    "regime": ctx.regime,
                }
            )

        # Optional: Exit if price re-breaks the range significantly
        if ctx.has_position():
            if ctx.position_direction() == "long" and current_price < lower_bound - 0.5 * atr:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "re_broke_lower_range_exit", "current_price": current_price, "lower_bound": lower_bound})
            if ctx.position_direction() == "short" and current_price > upper_bound + 0.5 * atr:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "re_broke_upper_range_exit", "current_price": current_price, "upper_bound": upper_bound})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        pass