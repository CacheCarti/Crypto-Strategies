from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityConditionedFade(Strategy):
    METADATA = {
        "name": "Volatility Conditioned Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Intended avg hold time (4 hours)
        "warmup_bars": 60,  # Need enough data for ATR and Z-score
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20  # For ATR and Z-score
        self.zscore_band_multiplier = 2.5 # Price outside this band
        self.atr_std_multiplier = 1.0 # For volatility quartile
        self.cooldown_bars = 5 # Bars to wait after a trade closes
        self.last_trade_bar = -self.cooldown_bars

        # Store recent ATR values to determine top quartile
        self.recent_atrs = []
        self.atr_history_size = 100 # How many ATR values to keep for quartile calculation

    def _atr(self, highs, lows, closes, period):
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

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if mean is None or std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period * 2) # Need more for stddev of ATR
        highs = ctx.highs(self.vol_period * 2)
        lows = ctx.lows(self.vol_period * 2)

        if len(closes) < self.vol_period * 2 or ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Calculate current ATR
        current_atr = self._atr(highs, lows, closes, self.vol_period)
        if current_atr is None:
            return None

        # Update recent ATRs for quartile calculation
        self.recent_atrs.append(current_atr)
        if len(self.recent_atrs) > self.atr_history_size:
            self.recent_atrs.pop(0)

        if len(self.recent_atrs) < self.atr_history_size: # Need enough ATRs to determine quartile
            return None

        # Determine volatility quartile (top 25%)
        sorted_atrs = sorted(self.recent_atrs)
        vol_threshold = sorted_atrs[int(len(sorted_atrs) * 0.75)] # Top quartile
        is_high_volatility = current_atr > vol_threshold

        # Calculate Z-score
        current_zscore = self._zscore(closes, self.vol_period)
        if current_zscore is None:
            return None

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Entry conditions:
        # 1. High volatility regime
        # 2. Price is stretched (outside z-score band)
        # 3. No open position

        if not ctx.has_position():
            if is_high_volatility:
                # Short signal: Price significantly above mean (positive z-score)
                if current_zscore > self.zscore_band_multiplier:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.75,
                                      stop_loss_bps=self.METADATA["declared_sl_bps"],
                                      take_profit_bps=self.METADATA["declared_tp_bps"],
                                      horizon_seconds=self.METADATA["declared_hold_seconds"],
                                      metadata={
                                          "reason": "volatility_conditioned_fade_short",
                                          "current_atr": current_atr,
                                          "vol_threshold": vol_threshold,
                                          "current_zscore": current_zscore,
                                          "zscore_band_multiplier": self.zscore_band_multiplier,
                                          "price": current_price
                                      })

                # Long signal: Price significantly below mean (negative z-score)
                elif current_zscore < -self.zscore_band_multiplier:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.75,
                                      stop_loss_bps=self.METADATA["declared_sl_bps"],
                                      take_profit_bps=self.METADATA["declared_tp_bps"],
                                      horizon_seconds=self.METADATA["declared_hold_seconds"],
                                      metadata={
                                          "reason": "volatility_conditioned_fade_long",
                                          "current_atr": current_atr,
                                          "vol_threshold": vol_threshold,
                                          "current_zscore": current_zscore,
                                          "zscore_band_multiplier": self.zscore_band_multiplier,
                                          "price": current_price
                                      })
        else:
            # Exit condition: Z-score normalizes (price returns within band)
            if ctx.position_direction() == "long" and current_zscore > -0.5: # Z-score moves back to neutral/positive
                return ctx.signal("flat", confidence=0.6,
                                  metadata={
                                      "reason": "zscore_normalization_exit_long",
                                      "current_zscore": current_zscore,
                                      "price": current_price
                                  })
            elif ctx.position_direction() == "short" and current_zscore < 0.5: # Z-score moves back to neutral/negative
                return ctx.signal("flat", confidence=0.6,
                                  metadata={
                                      "reason": "zscore_normalization_exit_short",
                                      "current_zscore": current_zscore,
                                      "price": current_price
                                  })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last trade bar on close to respect cooldown
        self.last_trade_bar = ctx.bar_index