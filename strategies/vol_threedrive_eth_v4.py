from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityThreeDriveFade(Strategy):
    METADATA = {
        "name": "Volatility Three Drive Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,  # Need enough bars for ATR and long EMA
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_period = 20
        self.momentum_period = 5
        self.min_drive_strength_atr_multiplier = 1.0 # Minimum move size for a drive
        self.max_retrace_ratio = 0.5 # Max retrace before next drive
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

        # For state tracking of drives
        self.current_drive_direction = None # "long" or "short"
        self.drive_count = 0
        self.last_drive_start_price = None
        self.last_drive_peak_price = None # For retracement check

        # Regime filters
        self.min_volatility_atr_multiplier = 0.5 # Only trade if ATR is above this threshold compared to recent average
        self.max_volatility_atr_multiplier = 2.0 # Avoid trading in extreme volatility
        self.trend_filter_ema_period = 50 # Use a slower EMA for trend filtering

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Calculate indicators
        atr = self._atr(highs, lows, closes, self.atr_period)
        ema = self._ema(closes, self.ema_period)
        sma_momentum = self._sma(closes, self.momentum_period)
        ema_trend = self._ema(closes, self.trend_filter_ema_period)

        if any(x is None for x in [atr, ema, sma_momentum, ema_trend]):
            return None

        # Regime filtering
        # Average ATR over a longer period to gauge baseline volatility
        long_term_atr = self._atr(highs, lows, closes, self.atr_period * 2)
        if long_term_atr is None or long_term_atr == 0:
            return None

        current_atr_ratio = atr / long_term_atr
        if not (self.min_volatility_atr_multiplier <= current_atr_ratio <= self.max_volatility_atr_multiplier):
            # Volatility is too low or too high
            self.reset_drive_state() # Reset if regime is not right
            return None

        # Trend filter: Only fade pushes against the prevailing trend
        # For long fades (short signals), we want an uptrend or neutral trend.
        # For short fades (long signals), we want a downtrend or neutral trend.
        if current_price > ema_trend:
            trend_regime = "bullish"
        elif current_price < ema_trend:
            trend_regime = "bearish"
        else:
            trend_regime = "neutral"

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Drive detection logic
        # A "drive" is a sustained move in one direction.
        # We are looking for three successive drives.

        if self.current_drive_direction is None:
            # No current drive, look for a new one to start
            if current_price > closes[-2] + atr * self.min_drive_strength_atr_multiplier:
                self.current_drive_direction = "long"
                self.drive_count = 1
                self.last_drive_start_price = closes[-2]
                self.last_drive_peak_price = current_price
            elif current_price < closes[-2] - atr * self.min_drive_strength_atr_multiplier:
                self.current_drive_direction = "short"
                self.drive_count = 1
                self.last_drive_start_price = closes[-2]
                self.last_drive_peak_price = current_price
        else:
            # Continue or re-evaluate existing drive
            if self.current_drive_direction == "long":
                if current_price > self.last_drive_peak_price:
                    # Drive continues
                    self.last_drive_peak_price = current_price
                elif current_price < self.last_drive_peak_price - (self.last_drive_peak_price - self.last_drive_start_price) * self.max_retrace_ratio:
                    # Significant retracement, drive might be over or failed.
                    # Or, it could be the end of a drive, and a new one starts if it pushes further.
                    # For a three-drive pattern, we need a slight retrace then a new push.
                    # This is the "momentum weakens and the next push fails" part.
                    if self.drive_count == 3:
                        # Third drive failed to maintain momentum after retrace
                        if not ctx.has_position() and (trend_regime == "bullish" or trend_regime == "neutral"):
                            self.last_signal_bar = ctx.bar_index
                            self.reset_drive_state()
                            return ctx.signal("short", confidence=0.75,
                                              metadata={"reason": "three_drive_long_fade",
                                                        "current_price": current_price,
                                                        "atr": atr,
                                                        "ema": ema,
                                                        "sma_momentum": sma_momentum,
                                                        "trend_regime": trend_regime,
                                                        "volatility_ratio": current_atr_ratio})
                    # Reset for a new drive if retracement was too deep for current drive
                    self.reset_drive_state()

                elif current_price > self.last_drive_peak_price + atr * self.min_drive_strength_atr_multiplier / 2:
                    # New drive in the same direction
                    self.drive_count += 1
                    self.last_drive_start_price = self.last_drive_peak_price # New start is previous peak
                    self.last_drive_peak_price = current_price

            elif self.current_drive_direction == "short":
                if current_price < self.last_drive_peak_price:
                    # Drive continues
                    self.last_drive_peak_price = current_price
                elif current_price > self.last_drive_peak_price + (self.last_drive_start_price - self.last_drive_peak_price) * self.max_retrace_ratio:
                    # Significant retracement, drive might be over or failed.
                    if self.drive_count == 3:
                        # Third drive failed to maintain momentum after retrace
                        if not ctx.has_position() and (trend_regime == "bearish" or trend_regime == "neutral"):
                            self.last_signal_bar = ctx.bar_index
                            self.reset_drive_state()
                            return ctx.signal("long", confidence=0.75,
                                              metadata={"reason": "three_drive_short_fade",
                                                        "current_price": current_price,
                                                        "atr": atr,
                                                        "ema": ema,
                                                        "sma_momentum": sma_momentum,
                                                        "trend_regime": trend_regime,
                                                        "volatility_ratio": current_atr_ratio})
                    # Reset for a new drive if retracement was too deep for current drive
                    self.reset_drive_state()

                elif current_price < self.last_drive_peak_price - atr * self.min_drive_strength_atr_multiplier / 2:
                    # New drive in the same direction
                    self.drive_count += 1
                    self.last_drive_start_price = self.last_drive_peak_price # New start is previous peak
                    self.last_drive_peak_price = current_price

        # Exit logic: if we are in a position and the 3-drive pattern completes, but we didn't exit,
        # or if the momentum shifts strongly against us.
        if ctx.has_position():
            # Simple momentum check to exit
            if ctx.position_direction() == "long" and current_price < ema: # Price falls below EMA
                self.last_signal_bar = ctx.bar_index
                self.reset_drive_state()
                return ctx.signal("flat", confidence=0.5,
                                  metadata={"reason": "ema_cross_exit_long",
                                            "current_price": current_price,
                                            "ema": ema})
            elif ctx.position_direction() == "short" and current_price > ema: # Price rises above EMA
                self.last_signal_bar = ctx.bar_index
                self.reset_drive_state()
                return ctx.signal("flat", confidence=0.5,
                                  metadata={"reason": "ema_cross_exit_short",
                                            "current_price": current_price,
                                            "ema": ema})

        return None

    def reset_drive_state(self):
        self.current_drive_direction = None
        self.drive_count = 0
        self.last_drive_start_price = None
        self.last_drive_peak_price = None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset drive state after any position close to ensure clean pattern detection
        self.reset_drive_state()
        # Optionally, adapt cooldown or confidence based on win/loss
        # For simplicity, we just reset the drive state and rely on the hard cooldown.
        pass