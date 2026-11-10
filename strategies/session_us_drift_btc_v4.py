from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class BTCSessionDrift(Strategy):
    METADATA = {
        "name": "BTCUSSessionDrift",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-hour target hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13
        self.us_session_end_hour = 21 # End of session, exit before this
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after an entry
        self.last_entry_bar = -self.entry_cooldown_bars
        self.min_session_move_factor = 0.005 # 0.5% move in session to qualify
        self.volatility_period = 20 # For median range
        self.max_spike_ratio = 0.6 # Max ratio of last bar's move to session move
        self.regime_filter_enabled = True # Enable/disable regime filtering
        self.min_fear_greed = 25 # Only trade if not extreme fear
        self.max_fear_greed = 75 # Only trade if not extreme greed

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        return statistics.median(ranges)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: if we have a position and it's near the end of the US session
        if ctx.has_position() and current_hour >= self.us_session_end_hour - 1: # Exit 1 hour before session end
            if ctx.position_direction() == "long" or ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Entry cooldown
        if ctx.bar_index - self.last_entry_bar < self.entry_cooldown_bars:
            return None

        # Regime filtering
        if self.regime_filter_enabled:
            trend_regime = ctx.market.get("trend_regime", "neutral")
            fear_greed = ctx.features.get("fear_greed_index", 50)
            
            # Avoid trading in strong bear trends or extreme market sentiment
            if trend_regime == "bear" or not (self.min_fear_greed <= fear_greed <= self.max_fear_greed):
                return None

        # US Session analysis (trigger at the start of the next bar after session end)
        # We analyze the session that just ended (i.e., if current_hour is 22, we look at 13-21)
        if current_hour == self.us_session_end_hour:
            # Find session bars
            session_bars_closes = []
            session_bars_opens = []
            session_bars_highs = []
            session_bars_lows = []
            
            # Look back to find the start of the US session
            # Max lookback for session is 9 bars (21-13 = 8 + current bar)
            lookback_depth = min(ctx.bar_index, 9) 
            
            session_start_idx = -1
            for i in range(1, lookback_depth + 1):
                bar_hour = (ctx.bar.timestamp.hour - i + 24) % 24 # Correctly calculate past hour
                if bar_hour == self.us_session_start_hour:
                    session_start_idx = i
                    break
            
            if session_start_idx == -1: # Did not find full session in recent history
                return None

            session_closes = closes[-session_start_idx:]
            session_opens = opens[-session_start_idx:]
            session_highs = highs[-session_start_idx:]
            session_lows = lows[-session_start_idx:]

            if not session_closes or len(session_closes) < 2:
                return None

            session_open = session_opens[0]
            session_close = session_closes[-1]
            session_high = max(session_highs)
            session_low = min(session_lows)

            session_move = session_close - session_open
            session_range = session_high - session_low
            
            median_recent_range = self._median_range(highs[:-session_start_idx], lows[:-session_start_idx], self.volatility_period)
            if median_recent_range is None or median_recent_range == 0:
                return None

            # Check if session move is decisive and not a single bar spike
            if abs(session_move) > self.min_session_move_factor * session_open and \
               abs(session_move) > 0.5 * median_recent_range: # Move must be significant compared to recent range
                
                # Prevent single-bar spikes from triggering
                last_bar_move = closes[-1] - opens[-1]
                if abs(last_bar_move) / abs(session_move) > self.max_spike_ratio:
                    return None # Session move dominated by a single spike

                signal_direction = None
                reason = ""
                confidence = 0.6

                if session_move > 0: # Bullish session, look for long
                    if not ctx.has_position() or ctx.position_direction() == "short":
                        signal_direction = "long"
                        reason = "us_session_bullish_drift"
                elif session_move < 0: # Bearish session, look for short
                    if not ctx.has_position() or ctx.position_direction() == "long":
                        signal_direction = "short"
                        reason = "us_session_bearish_drift"

                if signal_direction:
                    self.last_entry_bar = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": reason,
                            "session_move_pct": (session_move / session_open) * 100,
                            "session_range_pct": (session_range / session_open) * 100,
                            "median_recent_range": median_recent_range,
                            "current_price": ctx.bar.close,
                            "trend_regime": ctx.market.get("trend_regime", "neutral"),
                            "fear_greed": fear_greed
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic for now, just reset cooldown if needed (though it's bar-index based)
        pass