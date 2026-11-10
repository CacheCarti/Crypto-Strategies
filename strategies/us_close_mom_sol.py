from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USCloseMomentumSOL(Strategy):
    METADATA = {
        "name": "USCloseMomentumSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold until UTC 0 or stop, ~4 hours
        "warmup_bars": 50,  # Need enough bars for 4-hour momentum, ATR, and median range/volume
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 4  # Last 4 hours (4 bars for 1h timeframe)
        self.atr_period = 14
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_signal_bar_index = -self.cooldown_bars

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Check for cooldown
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: if position exists and it's UTC 0, or if it's past the intended hold time
        if ctx.has_position():
            # Exit at UTC 0 (end of day)
            if current_hour == 0:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "exit_utc_midnight", "current_hour": current_hour})
            # Check if it's time to exit based on declared_hold_seconds (approximate check)
            bars_held = ctx.bar_index - ctx.position["entry_bar_index"]
            if bars_held * 3600 > self.METADATA["declared_hold_seconds"]:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_max_hold_time", "bars_held": bars_held})
            return None # Hold position otherwise

        # Entry logic: Only consider entries at UTC 21 (US session close)
        if current_hour == 21:
            # Momentum calculation for the last self.momentum_period hours
            if len(closes) < self.momentum_period:
                return None

            momentum_closes = closes[-self.momentum_period:]
            momentum_opens = opens[-self.momentum_period:]
            momentum_highs = highs[-self.momentum_period:]
            momentum_lows = lows[-self.momentum_period:]
            momentum_volumes = volumes[-self.momentum_period:]

            # Calculate total price change over the momentum period
            price_change = closes[-1] - opens[-self.momentum_period]

            # Calculate average true range for volatility context
            atr = self._atr(highs, lows, closes, self.atr_period)
            if atr is None:
                return None

            # Calculate ranges and volumes for the momentum period
            ranges = [h - l for h, l in zip(momentum_highs, momentum_lows)]
            
            # Median range and volume over a longer period for comparison
            all_ranges = [h - l for h, l in zip(highs, lows)]
            all_volumes = volumes

            # Ensure enough data for median calculations
            if len(all_ranges) < self.warmup_bars or len(all_volumes) < self.warmup_bars:
                return None
            
            # Simple median approximation (not true median, but good enough for comparison)
            median_range_long = self._sma(sorted(all_ranges)[-self.warmup_bars:], self.warmup_bars // 2) # Approx. median of last N bars
            median_volume_long = self._sma(sorted(all_volumes)[-self.warmup_bars:], self.warmup_bars // 2) # Approx. median of last N bars

            if median_range_long is None or median_volume_long is None:
                return None

            # Aggregate range and volume for the momentum window
            momentum_total_range = sum(ranges)
            momentum_total_volume = sum(momentum_volumes)

            # Define "strong close" conditions
            # 1. Significant positive price change
            # 2. Above-median aggregated range
            # 3. Above-median aggregated volume
            
            # Long entry conditions
            is_strong_up_momentum = price_change > 0.5 * atr and \
                                    momentum_total_range > median_range_long * self.momentum_period * 0.8 and \
                                    momentum_total_volume > median_volume_long * self.momentum_period * 0.8

            # Short entry conditions (inverse of long)
            is_strong_down_momentum = price_change < -0.5 * atr and \
                                      momentum_total_range > median_range_long * self.momentum_period * 0.8 and \
                                      momentum_total_volume > median_volume_long * self.momentum_period * 0.8


            if is_strong_up_momentum:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_close_momentum_long",
                        "current_hour": current_hour,
                        "price_change_4h": price_change,
                        "atr": atr,
                        "momentum_total_range": momentum_total_range,
                        "median_range_long": median_range_long * self.momentum_period,
                        "momentum_total_volume": momentum_total_volume,
                        "median_volume_long": median_volume_long * self.momentum_period
                    }
                )
            elif is_strong_down_momentum:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_close_momentum_short",
                        "current_hour": current_hour,
                        "price_change_4h": price_change,
                        "atr": atr,
                        "momentum_total_range": momentum_total_range,
                        "median_range_long": median_range_long * self.momentum_period,
                        "momentum_total_volume": momentum_total_volume,
                        "median_volume_long": median_volume_long * self.momentum_period
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, allowing for a new signal sooner if conditions are met
        # This can be adjusted based on desired strategy behavior
        self.last_signal_bar_index = ctx.bar_index
        pass