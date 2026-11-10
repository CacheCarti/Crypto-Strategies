from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightFade(Strategy):
    METADATA = {
        "name": "EthOvernightFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # ~2 hours, to cover the fade
        "warmup_bars": 50,
        "required_features": [], # No external features used for this pure price-action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 6    # UTC 06:00
        self.fade_threshold_pct = 0.5  # % of ATR for a "stretched" move
        self.atr_period = 20
        self.cooldown_bars = 6 # Wait 6 bars (6 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow first trade

        # For daily trade tracking
        self.last_trade_day = -1
        self.traded_today = False

        # Regime filters
        self.max_crisis_score = 0.3 # Don't trade in high crisis
        self.min_calm_regime_confidence = 0.5 # Prefer calm/normal regimes

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(self.atr_period + 1)
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset daily trade flag at the start of a new day
        if current_day != self.last_trade_day:
            self.traded_today = False
            self.last_trade_day = current_day

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider entries within the session window
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            # If we have a position, close it at the end of the session window if it's still open
            if ctx.has_position() and current_hour == self.session_end_hour:
                self.last_trade_bar = ctx.bar_index # Cooldown after exit
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None

        # Ensure we only trade once per session
        if self.traded_today:
            return None

        # Regime filters
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > self.max_crisis_score:
            return None # Stand down in crisis

        # Trend regime check (avoid fading strong trends)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        if (trend_regime == "bull" and trend_confidence > 0.6) or \
           (trend_regime == "bear" and trend_confidence > 0.6):
            return None # Avoid fading strong trends

        # Calculate session open and range
        # Find the first bar of the current session
        session_opens = []
        session_highs = []
        session_lows = []
        session_closes = []
        for i in range(len(ctx.opens(self.METADATA["warmup_bars"])) - 1, -1, -1): # Iterate backwards to find session start
            bar_time = ctx.bar.timestamp - (ctx.bar_index - i) * (ctx.bar.timestamp - ctx.closes(len(ctx.closes()))[i]).total_seconds() / 3600 # Approx timestamp
            bar_hour = (ctx.bar.timestamp.hour - (ctx.bar_index - i)) % 24 # Crude hour calc
            
            # This is a bit tricky with only bar_index. Let's simplify:
            # Assume session starts at the exact hour. Find the price at session start.
            # This approximation might be off by one bar but is faster.
            if ctx.bar.timestamp.hour == self.session_start_hour and ctx.bar_index > self.session_start_hour:
                # This is the bar *after* the session open.
                # The actual session open price is ctx.opens(1)[0] at the session_start_hour.
                # We need the close of the bar *before* the session_start_hour if we want a true open.
                # For simplicity, let's use the open of the first bar of the session.
                # We need to collect prices from the start of the current session.
                # This requires iterating through historical bars, which is expensive.

                # Let's use a simpler approach: calculate the range from the first bar seen in the session.
                # This requires tracking session data. A simpler solution is to use the overnight low/high.
                # Given current bar is within session, find the min/max since session_start_hour.
                # This is still not ideal without a list of bars *within* the current session.

                # Alternative: Use the previous day's close as a reference for "midpoint".
                # Or, assume "session_midpoint" is the open of the first bar of the session.
                # This would require finding the open at ctx.bar_index - (current_hour - session_start_hour).
                # Let's try to get the session open price.
                session_open_idx_offset = current_hour - self.session_start_hour
                if session_open_idx_offset >= 0 and ctx.bar_index >= session_open_idx_offset:
                    session_open_price = ctx.opens(session_open_idx_offset + 1)[0]
                    
                    # Get bars since session open
                    session_closes = ctx.closes(session_open_idx_offset + 1)
                    session_highs = ctx.highs(session_open_idx_offset + 1)
                    session_lows = ctx.lows(session_open_idx_offset + 1)

                    if len(session_closes) <= 1: # Not enough bars in session yet
                        return None

                    session_high = max(session_highs)
                    session_low = min(session_lows)
                    
                    current_price = ctx.bar.close
                    
                    atr = self._atr(highs, lows, closes, self.atr_period)
                    if atr is None:
                        return None

                    stretch_amount = self.fade_threshold_pct * atr

                    # Check for long fade (price stretched down, fade up)
                    if current_price < session_low - stretch_amount: # Price broke below session low significantly
                        # We want to fade the downside stretch, so go long
                        # Only if we are not already long
                        if not ctx.has_position() or ctx.position_direction() == "short":
                            self.traded_today = True
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.75,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "overnight_long_fade_below_session_low",
                                    "current_price": current_price,
                                    "session_low": session_low,
                                    "stretch_amount": stretch_amount,
                                    "atr": atr,
                                    "crisis_score": crisis_score,
                                }
                            )
                    
                    # Check for short fade (price stretched up, fade down)
                    elif current_price > session_high + stretch_amount: # Price broke above session high significantly
                        # We want to fade the upside stretch, so go short
                        # Only if we are not already short
                        if not ctx.has_position() or ctx.position_direction() == "long":
                            self.traded_today = True
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.75,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "overnight_short_fade_above_session_high",
                                    "current_price": current_price,
                                    "session_high": session_high,
                                    "stretch_amount": stretch_amount,
                                    "atr": atr,
                                    "crisis_score": crisis_score,
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset the traded_today flag if needed, but it's already reset daily.
        # This could be used for adaptive sizing or logging.
        pass