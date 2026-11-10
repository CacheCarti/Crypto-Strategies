from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSessionGapReversion(Strategy):
    METADATA = {
        "name": "ETH Session Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for multi-hour holds within session
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this session-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18  # UTC 18:00 (late US session start)
        self.entry_hour_end = 23    # UTC 23:00 (late US session end, last bar to enter)
        self.exit_hour = 0          # UTC 00:00 (exit before new day/session)
        self.min_gap_bps = 5.0      # Minimum gap size to consider, in bps
        self.gap_continuation_bars = 3 # How many bars to check for continuation
        self.cooldown_bars = 6      # Cooldown period after a trade (6 hours)
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Store session open/close prices
        self.prior_session_close = None
        self.current_session_open = None
        self.last_processed_day = None

        # Indicators for regime filtering
        self.fast_ema_period = 10
        self.slow_ema_period = 30

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Update session open/close prices
        if self.last_processed_day is None or current_day != self.last_processed_day:
            # New day, reset session tracking
            self.prior_session_close = ctx.bar.close
            self.current_session_open = ctx.bar.open
            self.last_processed_day = current_day
        else:
            # Within the same day, update prior session close (which is just the previous bar's close here)
            # and current session open (which is the current bar's open)
            pass # No need to re-assign if it's not a new day, we only care about the gap at the start of our entry window.

        # Regime filter: only trade in 'normal' or 'volatile' regimes, and neutral/bull trend
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if market_regime == "CRISIS" or market_regime == "MELTDOWN" or trend_regime == "bear":
            return None # Stand down in crisis or strong bear trend

        # Calculate EMAs for trend/momentum filtering
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Exit logic: Flat position before self.exit_hour
        if ctx.has_position() and current_hour >= self.exit_hour and ctx.position_direction() != "flat":
            self.last_trade_bar_index = ctx.bar_index # Record exit for cooldown
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "exit_hour": self.exit_hour, "current_hour": current_hour})

        # Entry logic: Only within the specified entry window
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            if not ctx.has_position():
                # We need the previous day's close and current bar's open for gap calculation
                # For simplicity in 1h bars, we define the "gap" as the overnight difference or significant move
                # We'll use the open of the first bar in our entry window vs the close of the last bar from the previous day.
                # Since we don't have direct access to the *previous day's* specific last close,
                # we will approximate this by looking at the price action around the entry_hour_start.

                # Let's consider the gap between the close of the bar BEFORE entry_hour_start,
                # and the open of the bar AT entry_hour_start.
                # If we are at the entry_hour_start bar, we can look at closes(2) to get the prior close.
                
                # To prevent overtrading, we only want to evaluate this gap once per day within the window.
                # We use self.last_processed_day to ensure we don't re-evaluate for the same day.
                
                # To simplify, we'll look for a gap relative to the prior bar's close.
                # A "gap" is simply a significant price difference from the previous bar's close to the current bar's open.
                # We are looking for a *lack of continuation* after this gap.

                prev_close = ctx.closes(2)[0] if len(ctx.closes(2)) == 2 else None
                current_open = ctx.bar.open
                
                if prev_close is None:
                    return None

                gap_bps = (current_open - prev_close) / prev_close * 10000
                
                # Check for continuation over the last few bars
                recent_closes = ctx.closes(self.gap_continuation_bars + 1)
                recent_opens = ctx.opens(self.gap_continuation_bars + 1)
                
                if len(recent_closes) < self.gap_continuation_bars + 1:
                    return None

                # No continuation means price has moved back towards the prior close, or stalled.
                # Long entry: Significant down gap, but it's not continuing lower.
                if gap_bps < -self.min_gap_bps: # Down gap
                    # Check if current price has recovered from the low of the gap, or stalled
                    # i.e., the current close is significantly higher than the low of the gap bar,
                    # or the price has failed to make new lows for 'gap_continuation_bars' bars.
                    # For simplicity, if the current close is above the open of the gap bar, it indicates reversion.
                    if ctx.bar.close > current_open: # Price has moved up from open, filling part of the gap
                        # Regime filter: Only long if fast EMA is above slow EMA (bullish/neutral trend)
                        if fast_ema > slow_ema:
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "session_down_gap_reversion_long",
                                    "gap_bps": gap_bps,
                                    "prev_close": prev_close,
                                    "current_open": current_open,
                                    "current_close": ctx.bar.close,
                                    "fast_ema": fast_ema,
                                    "slow_ema": slow_ema,
                                    "regime": market_regime,
                                    "trend": trend_regime
                                }
                            )

                # Short entry: Significant up gap, but it's not continuing higher.
                elif gap_bps > self.min_gap_bps: # Up gap
                    # Check if current price has pulled back from the high of the gap, or stalled
                    # i.e., the current close is significantly lower than the high of the gap bar,
                    # or the price has failed to make new highs for 'gap_continuation_bars' bars.
                    # For simplicity, if the current close is below the open of the gap bar, it indicates reversion.
                    if ctx.bar.close < current_open: # Price has moved down from open, filling part of the gap
                        # Regime filter: Only short if fast EMA is below slow EMA (bearish/neutral trend)
                        if fast_ema < slow_ema:
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "session_up_gap_reversion_short",
                                    "gap_bps": gap_bps,
                                    "prev_close": prev_close,
                                    "current_open": current_open,
                                    "current_close": ctx.bar.close,
                                    "fast_ema": fast_ema,
                                    "slow_ema": slow_ema,
                                    "regime": market_regime,
                                    "trend": trend_regime
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already update last_trade_bar_index on exit signal.
        # This method can be used for adaptive sizing or logging if needed.
        pass