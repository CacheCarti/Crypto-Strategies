from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRejectionFade(Strategy):
    METADATA = {
        "name": "Volatility Rejection Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rejection_period = 10 # Bars to check for rejection wick
        self.confirmation_period = 3 # Bars for confirmation of rejection
        self.volume_ratio_threshold = 1.5 # Volume compared to average
        self.wick_body_ratio_threshold = 2.0 # Wick size compared to body size
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate average volume for comparison
        avg_volume_period = 30
        if len(volumes) < avg_volume_period:
            return None
        avg_volume = sum(volumes[-avg_volume_period:]) / avg_volume_period

        # Volatility check (using ATR or StdDev of closes)
        volatility_period = 20
        if len(closes) < volatility_period or len(highs) < volatility_period or len(lows) < volatility_period:
            return None
        std_dev_closes = self._stddev(closes, volatility_period)
        if std_dev_closes is None or std_dev_closes == 0:
            return None

        # --- Rejection Logic ---
        # Look for a large rejection wick on the previous bar (bar_index - 1)
        # and confirmation on the current bar (bar_index)

        # Bar - 1 (potential rejection bar)
        prev_open = opens[-2]
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]

        # Current Bar (confirmation bar)
        curr_open = opens[-1]
        curr_close = closes[-1]
        curr_high = highs[-1]
        curr_low = lows[-1]
        curr_volume = volumes[-1]

        # Check for significant volume on the potential rejection bar
        if prev_volume < avg_volume * self.volume_ratio_threshold:
            return None

        # Calculate body and wick sizes for the previous bar
        body_size_prev = abs(prev_open - prev_close)
        total_range_prev = prev_high - prev_low

        if total_range_prev == 0: # Avoid division by zero
            return None

        # Long rejection wick (upper wick) - potential short signal
        upper_wick_prev = prev_high - max(prev_open, prev_close)
        lower_wick_prev = min(prev_open, prev_close) - prev_low

        if upper_wick_prev > body_size_prev * self.wick_body_ratio_threshold and \
           upper_wick_prev > total_range_prev * 0.6: # Upper wick is at least 60% of total range
            # Confirmation for short: current bar closes lower than previous close and is bearish
            if curr_close < prev_close and curr_close < curr_open:
                # Ensure the current bar is not just a small doji, but a clear bearish follow-through
                if abs(curr_open - curr_close) > std_dev_closes * 0.5: # Body is at least 0.5 std dev
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            metadata={
                                "reason": "high_volume_upper_wick_rejection_confirmed",
                                "prev_high": prev_high,
                                "prev_close": prev_close,
                                "curr_close": curr_close,
                                "prev_volume_ratio": prev_volume / avg_volume,
                                "upper_wick_body_ratio": upper_wick_prev / (body_size_prev if body_size_prev > 0 else 0.01),
                                "std_dev_closes": std_dev_closes
                            }
                        )

        # Short rejection wick (lower wick) - potential long signal
        elif lower_wick_prev > body_size_prev * self.wick_body_ratio_threshold and \
             lower_wick_prev > total_range_prev * 0.6: # Lower wick is at least 60% of total range
            # Confirmation for long: current bar closes higher than previous close and is bullish
            if curr_close > prev_close and curr_close > curr_open:
                # Ensure the current bar is not just a small doji, but a clear bullish follow-through
                if abs(curr_open - curr_close) > std_dev_closes * 0.5: # Body is at least 0.5 std dev
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "high_volume_lower_wick_rejection_confirmed",
                                "prev_low": prev_low,
                                "prev_close": prev_close,
                                "curr_close": curr_close,
                                "prev_volume_ratio": prev_volume / avg_volume,
                                "lower_wick_body_ratio": lower_wick_prev / (body_size_prev if body_size_prev > 0 else 0.01),
                                "std_dev_closes": std_dev_closes
                            }
                        )

        # Exit logic (if position exists, but no new signal)
        if ctx.has_position():
            # If the market regime changes to crisis, consider exiting
            if ctx.market.get("regime", "NORMAL") == "CRISIS":
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "crisis_regime_exit",
                        "current_regime": ctx.market.get("regime")
                    }
                )
            # If the confirmation bar reverses against the rejection, consider exiting
            if ctx.position_direction() == "long":
                if curr_close < curr_open: # Current bar turns bearish after a long signal
                    if curr_close < ctx.position["entry_price"]:
                        return ctx.signal(
                            "flat",
                            confidence=0.6,
                            metadata={
                                "reason": "long_confirmation_failed_exit",
                                "curr_close": curr_close,
                                "curr_open": curr_open,
                                "entry_price": ctx.position["entry_price"]
                            }
                        )
            elif ctx.position_direction() == "short":
                if curr_close > curr_open: # Current bar turns bullish after a short signal
                    if curr_close > ctx.position["entry_price"]:
                        return ctx.signal(
                            "flat",
                            confidence=0.6,
                            metadata={
                                "reason": "short_confirmation_failed_exit",
                                "curr_close": curr_close,
                                "curr_open": curr_open,
                                "entry_price": ctx.position["entry_price"]
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        # Optional: Log position details or adapt strategy based on performance
        # print(f"Position closed: {position}")