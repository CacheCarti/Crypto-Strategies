from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityConditionedFadeBTC(Strategy):
    METADATA = {
        "name": "VolatilityConditionedFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Roughly 4 hours
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 40
        self.vol_period = 60
        self.zscore_threshold = 2.0
        self.vol_threshold_percentile = 0.75 # Top quartile
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

        # Store recent volatility for percentile calculation
        self.recent_volatilities = []

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _realized_volatility(self, closes, period):
        if len(closes) < period + 1:
            return None
        log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        if len(log_returns) < period:
            return None
        # Annualized volatility, but we just need a relative measure here
        return self._stddev(log_returns, period) * math.sqrt(252 * 24) # Daily bars * hours per day for annualization

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1)
        highs = ctx.highs(self.vol_period + 1)
        lows = ctx.lows(self.vol_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate Z-score
        z_score = self._zscore(closes, self.zscore_period)
        if z_score is None:
            return None

        # Calculate Realized Volatility
        realized_vol = self._realized_volatility(closes, self.vol_period)
        if realized_vol is None:
            return None
        
        self.recent_volatilities.append(realized_vol)
        if len(self.recent_volatilities) > 200: # Keep a rolling window of volatilities
            self.recent_volatilities.pop(0)

        # Determine volatility threshold
        if len(self.recent_volatilities) < 50: # Need enough data to calculate percentile
            return None
        
        sorted_vols = sorted(self.recent_volatilities)
        vol_index = int(len(sorted_vols) * self.vol_threshold_percentile)
        vol_threshold = sorted_vols[vol_index]

        # Condition 1: High Volatility Regime
        is_high_vol = realized_vol > vol_threshold

        # Condition 2: Stretched Price (outside Z-score band)
        is_stretched_up = z_score > self.zscore_threshold
        is_stretched_down = z_score < -self.zscore_threshold

        # Entry Logic
        if not ctx.has_position():
            if is_high_vol and is_stretched_up:
                # Fade the upside stretch in high vol
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "volatility_conditioned_fade_short",
                        "z_score": z_score,
                        "realized_volatility": realized_vol,
                        "vol_threshold": vol_threshold,
                        "price": current_price
                    }
                )
            elif is_high_vol and is_stretched_down:
                # Fade the downside stretch in high vol
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "volatility_conditioned_fade_long",
                        "z_score": z_score,
                        "realized_volatility": realized_vol,
                        "vol_threshold": vol_threshold,
                        "price": current_price
                    }
                )
        else:
            # Exit Logic: Z-score normalizes (comes back within bands)
            if ctx.position_direction() == "short" and z_score < self.zscore_threshold * 0.5: # Halfway back to mean
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "zscore_normalization_exit_short",
                        "current_z_score": z_score,
                        "entry_z_score": ctx.position["metadata"].get("z_score"),
                        "price": current_price
                    }
                )
            elif ctx.position_direction() == "long" and z_score > -self.zscore_threshold * 0.5: # Halfway back to mean
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "zscore_normalization_exit_long",
                        "current_z_score": z_score,
                        "entry_z_score": ctx.position["metadata"].get("z_score"),
                        "price": current_price
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy based on position close
        pass