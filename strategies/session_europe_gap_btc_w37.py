from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class GapReversionSessionStrategy(Strategy):
    METADATA = {
        "name": "BTC Gap Reversion Session",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Approx 1 hour bar hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7:00
        self.entry_hour_end = 10   # UTC 9:59 (so we can enter at 10:00 bar close)
        self.exit_hour = 12        # UTC 12:00
        self.cooldown_bars = 5     # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.last_day_traded = -1  # To ensure only one entry per day
        self.gap_threshold_bps = 25.0 # Minimum gap size in basis points
        self.continuation_bars = 2 # Bars to check for continuation
        self.ema_period = 20       # EMA for trend filter

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate EMA for trend filter
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        # Only consider trades during the European morning session
        if not (self.entry_hour_start <= current_hour < self.entry_hour_end):
            # If we are past the entry window but hold a position, check for time-based exit
            if ctx.has_position() and current_hour >= self.exit_hour:
                if ctx.position_direction() == "long":
                    reason = "time_based_exit_long_session_end"
                else:
                    reason = "time_based_exit_short_session_end"
                self.last_trade_bar = ctx.bar_index # Cooldown after exit
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "current_hour": current_hour})
            return None

        # Ensure only one entry per day (or rather, per 24h cycle within the session window)
        if current_day == self.last_day_traded:
            return None

        # Logic for gap detection and reversion
        # We need at least 2 full bars before current one to check for gap and continuation
        if len(opens) < 3: # Need opens[0], opens[1], opens[2]
            return None

        # Previous session's close (close of the bar before current session's open)
        # This assumes the "prior session close" refers to the close of the bar just before the open of the first bar of the new session.
        # For 1h bars, this means comparing ctx.opens[-1] (current bar's open) with ctx.closes[-2] (previous bar's close).
        # We want to detect a gap between the close of yesterday's last relevant bar and today's first relevant bar.
        # Let's define the "prior session close" as the close of the bar at UTC 6:00 (i.e. the bar ending at 7:00).
        # And the "next session open" as the open of the bar at UTC 7:00.

        # Find the close of the bar at UTC 6:00 (the last bar before our entry window)
        # We need to iterate back to find the specific hour's bar.
        # This is a bit tricky with `closes` which is relative to current bar.
        # Let's simplify: compare the current bar's open with the close of the bar 1 hour ago.
        # This acts as a proxy for a gap between the end of one period and the start of another.

        prev_close = closes[-2] # Close of the previous bar (ending 1 hour ago)
        current_open = opens[-1] # Open of the current bar (starting now)

        gap_bps = ((current_open - prev_close) / prev_close) * 10000

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Check for significant upward gap
        if gap_bps > self.gap_threshold_bps:
            # Check for lack of upward continuation in the first `continuation_bars` bars
            # Current bar is the first bar of the "continuation" check.
            # We need to see if price fails to move up further.
            # A simple check: if current close is below current open, or close is significantly below high.
            # Or if subsequent bars fail to hold the gap.

            # For simplicity, let's look at the current bar's close relative to open and previous close.
            # If it's a gap up but the current bar closes below its open (bearish reaction), it might revert.
            if closes[-1] < opens[-1] and (closes[-1] < prev_close):
                # Gap up, but immediately selling off and below prior close
                signal_direction = "short"
                reason = "gap_up_reversion_bearish_close"
                confidence = 0.7
            elif closes[-1] < ema_val: # Gap up, but current price is below EMA
                signal_direction = "short"
                reason = "gap_up_reversion_below_ema"
                confidence = 0.6
        # Check for significant downward gap
        elif gap_bps < -self.gap_threshold_bps:
            # Check for lack of downward continuation
            if closes[-1] > opens[-1] and (closes[-1] > prev_close):
                # Gap down, but immediately buying up and above prior close
                signal_direction = "long"
                reason = "gap_down_reversion_bullish_close"
                confidence = 0.7
            elif closes[-1] > ema_val: # Gap down, but current price is above EMA
                signal_direction = "long"
                reason = "gap_down_reversion_above_ema"
                confidence = 0.6

        if signal_direction and not ctx.has_position():
            # Add a funding rate filter: if funding is against our trade, reduce confidence or skip
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
            if signal_direction == "long" and funding_rate < -0.00005: # Very negative funding for longs
                confidence *= 0.7 # Reduce confidence
                reason += "_neg_funding_penalty"
            elif signal_direction == "short" and funding_rate > 0.00005: # Very positive funding for shorts
                confidence *= 0.7 # Reduce confidence
                reason += "_pos_funding_penalty"

            if confidence > 0.5: # Only trade if confidence is sufficient
                self.last_trade_bar = ctx.bar_index
                self.last_day_traded = current_day
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "gap_bps": round(gap_bps, 2),
                        "prev_close": round(prev_close, 2),
                        "current_open": round(current_open, 2),
                        "current_close": round(closes[-1], 2),
                        "ema_val": round(ema_val, 2),
                        "funding_rate": funding_rate
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt parameters
        pass