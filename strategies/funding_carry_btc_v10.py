from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingCarry(Strategy):
    METADATA = {
        "name": "BTC Funding Carry Strategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_fast = 10
        self.ema_period_slow = 30
        self.funding_threshold_long = -0.0001  # Negative funding (longs pay shorts)
        self.funding_threshold_short = 0.0001  # Positive funding (shorts pay longs)
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_trade_bar_index: Optional[int] = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if self.last_trade_bar_index is not None and \
           ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        
        fast_ema = self._ema(closes, self.ema_period_fast)
        slow_ema = self._ema(closes, self.ema_period_slow)

        if fast_ema is None or slow_ema is None:
            return None

        current_price = ctx.bar.close

        if ctx.has_position():
            # Check for conditions to exit current position
            if ctx.position_direction() == "long":
                # Exit long if funding normalizes or price crosses below slow EMA
                if current_funding_rate > self.funding_threshold_long / 2 or \
                   current_price < slow_ema:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_funding_normalized_or_price_below_ema",
                        "current_funding_rate": current_funding_rate,
                        "funding_threshold": self.funding_threshold_long / 2,
                        "current_price": current_price,
                        "slow_ema": slow_ema
                    })
            elif ctx.position_direction() == "short":
                # Exit short if funding normalizes or price crosses above slow EMA
                if current_funding_rate < self.funding_threshold_short / 2 or \
                   current_price > slow_ema:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_funding_normalized_or_price_above_ema",
                        "current_funding_rate": current_funding_rate,
                        "funding_threshold": self.funding_threshold_short / 2,
                        "current_price": current_price,
                        "slow_ema": slow_ema
                    })
            return None # Hold existing position if no exit condition met

        # Entry logic
        # Long entry: Negative funding (longs pay shorts) AND fast EMA crosses above slow EMA
        if current_funding_rate < self.funding_threshold_long and \
           fast_ema > slow_ema and \
           ctx.closes(self.ema_period_slow)[-2] < self._ema(ctx.closes(self.ema_period_slow)[0:-1], self.ema_period_slow): # Previous bar fast EMA was below slow EMA
            
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "funding_carry_long_ema_cross",
                "current_funding_rate": current_funding_rate,
                "funding_threshold": self.funding_threshold_long,
                "fast_ema": fast_ema,
                "slow_ema": slow_ema,
                "current_price": current_price
            })

        # Short entry: Positive funding (shorts pay longs) AND fast EMA crosses below slow EMA
        elif current_funding_rate > self.funding_threshold_short and \
             fast_ema < slow_ema and \
             ctx.closes(self.ema_period_slow)[-2] > self._ema(ctx.closes(self.ema_period_slow)[0:-1], self.ema_period_slow): # Previous bar fast EMA was above slow EMA
            
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "funding_carry_short_ema_cross",
                "current_funding_rate": current_funding_rate,
                "funding_threshold": self.funding_threshold_short,
                "fast_ema": fast_ema,
                "slow_ema": slow_ema,
                "current_price": current_price
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already set last_trade_bar_index upon signaling 'flat'
        pass