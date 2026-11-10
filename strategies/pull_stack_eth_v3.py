from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StructuralPullback(Strategy):
    METADATA = {
        "name": "StructuralPullback",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 8
        self.slow_ema_period = 21
        self.rsi_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # State for tracking higher highs/lows and pullbacks
        self.last_high = 0.0
        self.last_low = 0.0
        self.trend_direction = "neutral" # "up", "down", "neutral"
        self.pullback_detected = False
        self.pullback_entry_price = 0.0

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
        closes = ctx.closes(self.slow_ema_period + self.rsi_period)
        highs = ctx.highs(self.slow_ema_period + self.rsi_period)
        lows = ctx.lows(self.slow_ema_period + self.rsi_period)

        if len(closes) < self.slow_ema_period + self.rsi_period:
            return None

        current_price = ctx.bar.close
        last_close = closes[-2] if len(closes) >= 2 else current_price
        
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None
        
        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Trend detection (Higher Highs, Higher Lows for uptrend)
        # We need at least two swings to confirm a trend
        if self.last_high == 0.0 or self.last_low == 0.0:
            self.last_high = ctx.bar.high
            self.last_low = ctx.bar.low
            return None

        # Update last high/low only if new highs/lows are made
        # This is a simplified approach to structural points
        if current_price > self.last_high and self.trend_direction != "up":
            self.trend_direction = "up"
            self.last_high = current_price
            self.last_low = closes[-2] # Previous bar close as reference low
            self.pullback_detected = False # Reset pullback flag on new trend
        elif current_price < self.last_low and self.trend_direction != "down":
            self.trend_direction = "down"
            self.last_low = current_price
            self.last_high = closes[-2] # Previous bar close as reference high
            self.pullback_detected = False # Reset pullback flag on new trend
        elif self.trend_direction == "up" and current_price < self.last_high:
             # Price is pulling back from the high, update last_low if it's a new lower low
             self.last_low = min(self.last_low, current_price)
        elif self.trend_direction == "down" and current_price > self.last_low:
             # Price is pulling back from the low, update last_high if it's a new higher high
             self.last_high = max(self.last_high, current_price)


        # Entry logic: Long only
        if not ctx.has_position():
            # Condition 1: Confirmed Uptrend (higher highs, higher lows)
            # Simplified: EMA crossover suggests uptrend
            if fast_ema > slow_ema and last_close < fast_ema and current_price > fast_ema: # Price dipping below fast EMA and bouncing back above it
                if self.trend_direction == "up": # Only consider pullbacks in an established uptrend
                    # Condition 2: Price pulled back towards slow EMA but is now turning up
                    # Check for RSI divergence or oversold condition in pullback
                    if rsi < 45 and current_price > last_close: # RSI shows weakness, but price is recovering
                        if not self.pullback_detected or current_price < self.pullback_entry_price: # Only enter the first pullback or a deeper one
                            self.pullback_detected = True
                            self.pullback_entry_price = current_price
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.75,
                                metadata={
                                    "reason": "structural_pullback_bounce_uptrend_rsi_reversal",
                                    "fast_ema": fast_ema,
                                    "slow_ema": slow_ema,
                                    "rsi": rsi,
                                    "current_price": current_price,
                                    "trend_direction": self.trend_direction,
                                    "last_high": self.last_high,
                                    "last_low": self.last_low
                                }
                            )
        else: # Position is open
            position = ctx.position
            # Exit logic for long position
            if position["direction"] == "long":
                # Exit Condition 1: Structure breaks (price breaks below last significant low in uptrend)
                # Simplified: Price closes below slow EMA
                if current_price < slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    self.pullback_detected = False # Reset for next setup
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "structure_break_below_slow_ema",
                            "current_price": current_price,
                            "slow_ema": slow_ema,
                            "entry_price": position["entry_price"]
                        }
                    )
                # Exit Condition 2: Price reaches near previous high (take profit)
                # A little below to improve hit rate
                if current_price >= self.last_high * 0.99: # 1% below previous high
                    self.last_trade_bar = ctx.bar_index
                    self.pullback_detected = False # Reset for next setup
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "approaching_prior_high_profit_take",
                            "current_price": current_price,
                            "last_high": self.last_high,
                            "entry_price": position["entry_price"]
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset pullback detection to allow new setups after a position closes
        self.pullback_detected = False
        self.pullback_entry_price = 0.0
        # Potentially adapt self.cooldown_bars based on return_bps
        # For simplicity, we keep it static for now.
        pass