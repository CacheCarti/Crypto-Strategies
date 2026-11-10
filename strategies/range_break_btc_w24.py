from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutReversion(Strategy):
    METADATA = {
        "name": "Failed Breakout Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour holds
        "warmup_bars": 50,  # Need enough bars for range and EMA
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20  # Bars to define the consolidation range
        self.breakout_confirmation_period = 3 # Bars to confirm breakout
        self.reversion_entry_period = 2 # Bars to confirm reversion back into range
        self.ema_period = 20 # For trend filter
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trading immediately

        # Confidence parameters
        self.base_confidence = 0.6
        self.fng_boost_threshold = 40 # Fear & Greed index below this gives a boost
        self.funding_boost_threshold = 0.0001 # Negative funding rate gives a boost for longs

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + self.breakout_confirmation_period + self.reversion_entry_period + 5)
        highs = ctx.highs(self.range_period + self.breakout_confirmation_period + self.reversion_entry_period + 5)
        lows = ctx.lows(self.range_period + self.breakout_confirmation_period + self.reversion_entry_period + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate EMA for trend filtering
        ema_values = closes[-self.ema_period:]
        current_ema = self._ema(ema_values, self.ema_period)
        if current_ema is None:
            return None

        # Define the consolidation range (excluding the most recent bars that might be the breakout)
        range_closes = closes[-(self.range_period + self.breakout_confirmation_period + self.reversion_entry_period) : -(self.breakout_confirmation_period + self.reversion_entry_period)]
        range_highs = highs[-(self.range_period + self.breakout_confirmation_period + self.reversion_entry_period) : -(self.breakout_confirmation_period + self.reversion_entry_period)]
        range_lows = lows[-(self.range_period + self.breakout_confirmation_period + self.reversion_entry_period) : -(self.breakout_confirmation_period + self.reversion_entry_period)]

        if len(range_closes) < self.range_period:
            return None

        # Calculate range boundaries
        range_high = max(range_highs)
        range_low = min(range_lows)
        range_mid = (range_high + range_low) / 2.0
        range_size_bps = (range_high - range_low) / range_low * 10000

        # Filter out very narrow or very wide ranges
        if range_size_bps < 50 or range_size_bps > 500: # Min 0.5% range, Max 5% range
            return None

        # Check for failed breakout
        # A breakout is confirmed if price closes outside the range for 'breakout_confirmation_period' bars
        # A failed breakout is when it returns within the range after that.

        # Look at the bars immediately after the consolidation range
        breakout_candidates_closes = closes[-(self.breakout_confirmation_period + self.reversion_entry_period) : -self.reversion_entry_period]
        reversion_candidates_closes = closes[-self.reversion_entry_period:]

        if len(breakout_candidates_closes) < self.breakout_confirmation_period or len(reversion_candidates_closes) < self.reversion_entry_period:
            return None

        # --- Long Setup: Failed Bearish Breakout ---
        # 1. Price moved below range_low for `breakout_confirmation_period` bars
        # 2. Price then moved back inside the range for `reversion_entry_period` bars
        # 3. Current price is above EMA (uptrend filter)
        # 4. Fear & Greed is low (sentiment boost) OR funding rate is negative (long boost)

        is_bearish_breakout = all(c < range_low for c in breakout_candidates_closes)
        is_reverted_long = all(c > range_low for c in reversion_candidates_closes) and current_price > range_low

        if is_bearish_breakout and is_reverted_long and current_price > current_ema:
            if not ctx.has_position():
                confidence = self.base_confidence
                fng = ctx.features.get("fear_greed_index", 50)
                funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

                if fng < self.fng_boost_threshold:
                    confidence += 0.1
                if funding_rate < -self.funding_boost_threshold:
                    confidence += 0.1

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=min(confidence, 1.0),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_bearish_breakout_reversion",
                        "entry_price": current_price,
                        "range_high": range_high,
                        "range_low": range_low,
                        "range_mid": range_mid,
                        "ema_20": current_ema,
                        "fng_index": fng,
                        "funding_rate": funding_rate,
                    }
                )

        # --- Short Setup: Failed Bullish Breakout ---
        # 1. Price moved above range_high for `breakout_confirmation_period` bars
        # 2. Price then moved back inside the range for `reversion_entry_period` bars
        # 3. Current price is below EMA (downtrend filter)
        # 4. Fear & Greed is high (sentiment boost) OR funding rate is positive (short boost)

        is_bullish_breakout = all(c > range_high for c in breakout_candidates_closes)
        is_reverted_short = all(c < range_high for c in reversion_candidates_closes) and current_price < range_high

        if is_bullish_breakout and is_reverted_short and current_price < current_ema:
            if not ctx.has_position():
                confidence = self.base_confidence
                fng = ctx.features.get("fear_greed_index", 50)
                funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

                if fng > (100 - self.fng_boost_threshold): # High Fear & Greed
                    confidence += 0.1
                if funding_rate > self.funding_boost_threshold:
                    confidence += 0.1

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=min(confidence, 1.0),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_bullish_breakout_reversion",
                        "entry_price": current_price,
                        "range_high": range_high,
                        "range_low": range_low,
                        "range_mid": range_mid,
                        "ema_20": current_ema,
                        "fng_index": fng,
                        "funding_rate": funding_rate,
                    }
                )

        # --- Exit Logic ---
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            direction = position["direction"]

            # Exit if price reaches the opposite range edge or mid-point
            if direction == "long":
                if current_price >= range_high: # Reached top of the original range
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "long_exit_opposite_range_edge", "current_price": current_price, "range_high": range_high})
                if current_price >= range_mid and entry_price < range_mid: # Reached midpoint from below
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_exit_range_midpoint", "current_price": current_price, "range_mid": range_mid})
            elif direction == "short":
                if current_price <= range_low: # Reached bottom of the original range
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "short_exit_opposite_range_edge", "current_price": current_price, "range_low": range_low})
                if current_price <= range_mid and entry_price > range_mid: # Reached midpoint from above
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_exit_range_midpoint", "current_price": current_price, "range_mid": range_mid})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_trade_bar = ctx.bar_index
        pass