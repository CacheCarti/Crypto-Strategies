from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGreedFade(Strategy):
    METADATA = {
        "name": "SentimentGreedFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_short_period = 5
        self.ema_long_period = 20
        self.rsi_period = 14
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_long_period + self.rsi_period)
        if len(closes) < self.ema_long_period + self.rsi_period:
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None # Cooldown active

        current_price = ctx.bar.close
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)
        rsi = self._rsi(closes, self.rsi_period)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        if ema_short is None or ema_long is None or rsi is None:
            return None

        if ctx.has_position():
            # If in a long position and conditions turn bearish, consider flipping or flattening
            if ctx.position_direction() == "long":
                # Exit long if EMA short crosses below EMA long (bearish momentum)
                if ema_short < ema_long and closes[-2] > ema_short and closes[-2] > ema_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "ema_bearish_cross_exit_long", "ema_short": ema_short, "ema_long": ema_long, "rsi": rsi})
                # Exit long if RSI becomes overbought again (potential reversal)
                if rsi > 70:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "rsi_overbought_exit_long", "rsi": rsi, "ema_short": ema_short, "ema_long": ema_long})
            
            # If in a short position and conditions turn bullish, consider flipping or flattening
            elif ctx.position_direction() == "short":
                # Exit short if EMA short crosses above EMA long (bullish momentum)
                if ema_short > ema_long and closes[-2] < ema_short and closes[-2] < ema_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "ema_bullish_cross_exit_short", "ema_short": ema_short, "ema_long": ema_long, "rsi": rsi})
                # Exit short if RSI becomes oversold again (potential reversal)
                if rsi < 30:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "rsi_oversold_exit_short", "rsi": rsi, "ema_short": ema_short, "ema_long": ema_long})
            
            return ctx.signal("hold") # Hold existing position if no exit condition met

        # Entry logic
        # Fade rallies only when sentiment is extremely greedy AND momentum loses persistence (EMA short crosses below EMA long)
        if fear_greed_index >= 75: # Extreme Greed
            # Check for bearish EMA crossover (momentum loss)
            if ema_short < ema_long and closes[-2] > ema_short and closes[-2] > ema_long:
                # Confirm with RSI overbought or turning down
                if rsi > 60 or (rsi < self._rsi(closes[:-1], self.rsi_period) and self._rsi(closes[:-1], self.rsi_period) is not None):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "greed_fade_ema_cross_bearish_rsi_confirm",
                            "fear_greed_index": fear_greed_index,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )

        # Optional: Counter-trend long during extreme fear and bullish momentum reversal
        if fear_greed_index <= 25: # Extreme Fear
            # Check for bullish EMA crossover (momentum gain)
            if ema_short > ema_long and closes[-2] < ema_short and closes[-2] < ema_long:
                # Confirm with RSI oversold or turning up
                if rsi < 40 or (rsi > self._rsi(closes[:-1], self.rsi_period) and self._rsi(closes[:-1], self.rsi_period) is not None):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "fear_bounce_ema_cross_bullish_rsi_confirm",
                            "fear_greed_index": fear_greed_index,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "rsi": rsi,
                            "price": current_price
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, if needed. No signal can be returned here.
        pass