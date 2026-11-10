from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapFade(Strategy):
    METADATA = {
        "name": "Session Gap Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 7200, # Aim for 2-3 hours
        "warmup_bars": 50, # Need enough bars for EMA and gap calculations
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.gap_threshold_bps = 50.0  # Minimum gap size in bps to consider
        self.continuation_threshold_bps = 20.0 # How much price moves away from gap in first hour
        self.ema_period = 20 # EMA for trend filter
        self.cooldown_bars = 6 # Cooldown period after a trade

        self.last_trade_bar_index = -self.cooldown_bars

        # Store last US session close for gap calculation
        self.us_session_close_price: Optional[float] = None
        self.us_session_close_bar_index: Optional[int] = None
        self.us_session_close_timestamp: Optional[Any] = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # +1 for EMA
        opens = ctx.opens(self.warmup_bars + 1)
        
        if len(closes) < self.warmup_bars + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday=0, Sunday=6

        # Update US session close
        # US session typically closes around 21:00-22:00 UTC for crypto
        # We'll use 21:00 UTC as the close marker for simplicity
        if current_hour == 21:
            self.us_session_close_price = ctx.bar.close
            self.us_session_close_bar_index = ctx.bar_index
            self.us_session_close_timestamp = ctx.bar.timestamp
        
        # Only evaluate gaps during Asian session (e.g., 00:00 - 08:00 UTC)
        # And only if we have a recorded US session close from the previous day/session
        if (current_hour >= 0 and current_hour <= 8) and self.us_session_close_price is not None:
            # Ensure the US session close is from a previous day/session, not the current one
            if self.us_session_close_bar_index == ctx.bar_index:
                return None # Still in the same bar as the US close, wait for next bar

            # Calculate gap from previous US close to current bar's open
            gap = ctx.bar.open - self.us_session_close_price
            gap_bps = (gap / self.us_session_close_price) * 10000

            # Check for minimum gap size
            if abs(gap_bps) < self.gap_threshold_bps:
                return None

            # Check for continuation (price moving further in gap direction)
            # We look at the current bar's close relative to its open
            continuation_move_bps = ((ctx.bar.close - ctx.bar.open) / ctx.bar.open) * 10000
            
            # If gap is up, and price continues up significantly (or vice versa), it's a strong trend, not a fade
            if (gap_bps > 0 and continuation_move_bps > self.continuation_threshold_bps) or \
               (gap_bps < 0 and continuation_move_bps < -self.continuation_threshold_bps):
                return None # Gap continuation, not a fade opportunity

            # Cooldown check
            if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
                return None

            # Trend filter with EMA
            ema = self._ema(closes, self.ema_period)
            if ema is None:
                return None

            # Fear & Greed Index as a sentiment filter (avoid trading against strong sentiment)
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            
            # Entry logic: Fade the gap if conditions met
            signal_direction: Optional[str] = None
            confidence = 0.0
            reason_metadata = {}

            # Long signal: Negative gap (opened lower than US close) but didn't continue down
            if gap_bps < -self.gap_threshold_bps:
                # Price is below EMA, indicating downtrend, so fading a negative gap is counter-trend.
                # Only take if price is relatively close to EMA, or F&G is not extremely bearish.
                if ctx.bar.close > ema and fear_greed_index > 20: # Not extreme fear
                    signal_direction = "long"
                    confidence = min(0.7, abs(gap_bps) / 100.0) # Scale confidence with gap size
                    reason_metadata = {
                        "reason": "asia_session_negative_gap_fade_long",
                        "gap_bps": round(gap_bps, 2),
                        "continuation_bps": round(continuation_move_bps, 2),
                        "us_close": self.us_session_close_price,
                        "current_open": ctx.bar.open,
                        "current_close": ctx.bar.close,
                        "ema": round(ema, 2),
                        "fear_greed_index": fear_greed_index,
                    }
            # Short signal: Positive gap (opened higher than US close) but didn't continue up
            elif gap_bps > self.gap_threshold_bps:
                # Price is above EMA, indicating uptrend, so fading a positive gap is counter-trend.
                # Only take if price is relatively close to EMA, or F&G is not extremely bullish.
                if ctx.bar.close < ema and fear_greed_index < 80: # Not extreme greed
                    signal_direction = "short"
                    confidence = min(0.7, abs(gap_bps) / 100.0) # Scale confidence with gap size
                    reason_metadata = {
                        "reason": "asia_session_positive_gap_fade_short",
                        "gap_bps": round(gap_bps, 2),
                        "continuation_bps": round(continuation_move_bps, 2),
                        "us_close": self.us_session_close_price,
                        "current_open": ctx.bar.open,
                        "current_close": ctx.bar.close,
                        "ema": round(ema, 2),
                        "fear_greed_index": fear_greed_index,
                    }

            if signal_direction and not ctx.has_position():
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata=reason_metadata
                )

        # Position management: If we have a position, check for gap fill or time limit
        if ctx.has_position():
            position = ctx.position
            # Check if current price has filled the gap
            if position["direction"] == "long" and ctx.bar.close >= self.us_session_close_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "gap_filled_long",
                    "entry_price": position["entry_price"],
                    "current_price": ctx.bar.close,
                    "target_price": self.us_session_close_price
                })
            elif position["direction"] == "short" and ctx.bar.close <= self.us_session_close_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "gap_filled_short",
                    "entry_price": position["entry_price"],
                    "current_price": ctx.bar.close,
                    "target_price": self.us_session_close_price
                })
            
            # Exit before next US session (e.g. 20:00 UTC) to avoid holding through US close
            if current_hour == 20:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_limit_pre_us_close",
                    "current_price": ctx.bar.close
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not using this for adaptive logic for simplicity, but could be used
        # to adjust gap_threshold_bps or confidence based on recent performance.
        pass