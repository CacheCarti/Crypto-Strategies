from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFadeStrategy(Strategy):
    METADATA = {
        "name": "VolatilityFadeStrategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-2 hours on 1h bars
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 20
        self.short_period = 5
        self.atr_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # State for tracking directional pushes
        self.consecutive_pushes = 0
        self.last_push_direction = None
        self.last_push_start_price = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs: return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + self.atr_period)
        highs = ctx.highs(self.long_period + self.atr_period)
        lows = ctx.lows(self.long_period + self.atr_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        short_ema = self._ema(closes, self.short_period)
        long_ema = self._ema(closes, self.long_period)
        atr_val = self._atr(highs, lows, closes, self.atr_period)

        if short_ema is None or long_ema is None or atr_val is None:
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None # Cooldown active

        # Volatility filter: only trade during normal or high volatility
        if ctx.market.get("regime", "NORMAL") not in ["NORMAL", "HIGH_VOL"]:
            return None

        # Check for directional push based on EMA crossover and subsequent failure
        # A "push" is defined by price moving consistently in one direction, confirmed by short EMA
        # We are looking for the THIRD push to fail
        
        # Determine current momentum direction
        momentum_direction = None
        if short_ema > long_ema and closes[-1] > closes[-2]:
            momentum_direction = "up"
        elif short_ema < long_ema and closes[-1] < closes[-2]:
            momentum_direction = "down"

        # Track consecutive pushes
        if momentum_direction:
            if momentum_direction == self.last_push_direction:
                self.consecutive_pushes += 1
            else:
                self.consecutive_pushes = 1
                self.last_push_direction = momentum_direction
                self.last_push_start_price = current_close
        else:
            self.consecutive_pushes = 0
            self.last_push_direction = None
            self.last_push_start_price = None

        # Entry logic: fade the third successive directional push if it shows signs of failure
        if self.consecutive_pushes >= 3:
            # Check for failure of the third push
            if self.last_push_direction == "up":
                # Up push failure: current price is below the start of the third push
                # and shows reversal signs (e.g., current bar close is below previous close)
                if self.last_push_start_price and current_close < self.last_push_start_price and current_close < closes[-2]:
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "fade_third_up_push_failure",
                                "current_close": current_close,
                                "short_ema": short_ema,
                                "long_ema": long_ema,
                                "atr": atr_val,
                                "consecutive_pushes": self.consecutive_pushes,
                                "last_push_start_price": self.last_push_start_price,
                                "regime": ctx.market.get("regime", "NORMAL"),
                            }
                        )
            elif self.last_push_direction == "down":
                # Down push failure: current price is above the start of the third push
                # and shows reversal signs (e.g., current bar close is above previous close)
                if self.last_push_start_price and current_close > self.last_push_start_price and current_close > closes[-2]:
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "fade_third_down_push_failure",
                                "current_close": current_close,
                                "short_ema": short_ema,
                                "long_ema": long_ema,
                                "atr": atr_val,
                                "consecutive_pushes": self.consecutive_pushes,
                                "last_push_start_price": self.last_push_start_price,
                                "regime": ctx.market.get("regime", "NORMAL"),
                            }
                        )
        
        # If in a position, do nothing and let the engine manage SL/TP or horizon
        if ctx.has_position():
            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset push tracking on position close to avoid re-entering immediately on the same setup
        self.consecutive_pushes = 0
        self.last_push_direction = None
        self.last_push_start_price = None
        # Cooldown is already handled in on_bar, this just ensures state reset.