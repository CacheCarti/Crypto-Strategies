from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ClimaxReversal(Strategy):
    METADATA = {
        "name": "Climax Reversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for a few hours hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20
        self.vol_mult = 2.0  # Volume multiplier for climax
        self.range_mult = 2.0  # Range multiplier for climax
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)
        volumes = ctx.volumes(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1 or len(volumes) < self.lookback_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume
        current_range = current_high - current_low

        # Calculate average volume and range over lookback period
        avg_volume = self._sma(volumes[:-1], self.lookback_period)
        avg_range = self._sma([h - l for h, l in zip(highs[:-1], lows[:-1])], self.lookback_period)

        if avg_volume is None or avg_range is None:
            return None

        # Climax detection conditions
        is_climax_volume = current_volume > avg_volume * self.vol_mult
        is_climax_range = current_range > avg_range * self.range_mult

        # Check for extended move (e.g., above/below a moving average)
        ema_long = self._ema(closes, 40)
        if ema_long is None:
            return None

        prev_close = closes[-2]
        prev_prev_close = closes[-3]
        
        signal_direction = None
        reason = ""
        
        # Long Climax Reversal: Price makes a strong move down (climax bar) after an uptrend or range,
        # then next bar fails to continue down, showing signs of reversal.
        if is_climax_volume and is_climax_range and current_close < prev_close: # Climax down move
            # Check if previous bars were generally moving up or neutral before the climax
            if prev_close > ema_long and current_close < ema_long: # Price broke below EMA after being above
                # Check for fading continuation: current bar closed higher than its open, or did not make new lows
                if ctx.bar.close > ctx.bar.open or current_low > lows[-2]:
                    # Also check that the previous bar wasn't already a strong reversal signal
                    if prev_close > prev_prev_close: # The bar before the climax was up or flat
                        if not ctx.has_position():
                            signal_direction = "long"
                            reason = "climax_reversal_long_after_extended_down_move"

        # Short Climax Reversal: Price makes a strong move up (climax bar) after a downtrend or range,
        # then next bar fails to continue up, showing signs of reversal.
        elif is_climax_volume and is_climax_range and current_close > prev_close: # Climax up move
            # Check if previous bars were generally moving down or neutral before the climax
            if prev_close < ema_long and current_close > ema_long: # Price broke above EMA after being below
                # Check for fading continuation: current bar closed lower than its open, or did not make new highs
                if ctx.bar.close < ctx.bar.open or current_high < highs[-2]:
                    # Also check that the previous bar wasn't already a strong reversal signal
                    if prev_close < prev_prev_close: # The bar before the climax was down or flat
                        if not ctx.has_position():
                            signal_direction = "short"
                            reason = "climax_reversal_short_after_extended_up_move"

        # Exit logic
        if ctx.has_position():
            # Exit if price retraces a certain amount or after a short hold
            entry_price = ctx.position["entry_price"]
            if ctx.position_direction() == "long":
                # If price moves significantly against us after entry, consider exiting
                if current_close < entry_price * (1 - 0.005): # 0.5% against us
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_climax_exit_retracement_against", "entry_price": entry_price, "current_price": current_close})
            elif ctx.position_direction() == "short":
                # If price moves significantly against us after entry, consider exiting
                if current_close > entry_price * (1 + 0.005): # 0.5% against us
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_climax_exit_retracement_against", "entry_price": entry_price, "current_price": current_close})

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            btc_return = ctx.features.get("btc_return_pct", 0.0)
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

            confidence = 0.7 # Base confidence
            if signal_direction == "long" and btc_return < -0.5: # More confident on long if market is down
                confidence += 0.1
            elif signal_direction == "short" and btc_return > 0.5: # More confident on short if market is up
                confidence += 0.1
            
            # Adjust confidence based on funding rates for shorts (less confident if funding is very negative)
            if signal_direction == "short" and funding_rate < -0.0001:
                confidence -= 0.1
            # Adjust confidence based on funding rates for longs (less confident if funding is very positive)
            elif signal_direction == "long" and funding_rate > 0.0001:
                confidence -= 0.1

            confidence = max(0.1, min(1.0, confidence)) # Clamp confidence

            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_close,
                    "current_volume": current_volume,
                    "current_range": current_range,
                    "avg_volume_20": avg_volume,
                    "avg_range_20": avg_range,
                    "ema_40": ema_long,
                    "btc_return_pct": btc_return,
                    "funding_rate_btcusdt": funding_rate
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        # You could also log position stats here or adjust future parameters
        pass