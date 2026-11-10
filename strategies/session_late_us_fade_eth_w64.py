from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class LateUSSessionFade(Strategy):
    METADATA = {
        "name": "Late US Session Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_length_hours = 6 # 18:00 UTC to 24:00 UTC
        self.max_session_bars = self.session_length_hours
        self.lookback_period = 20 # For session range and midpoint
        self.entry_threshold_pct = 0.5 # Price needs to be 0.5% away from midpoint
        self.entry_cooldown_bars = 5
        self.bars_since_last_trade = 0
        self.daily_trade_flag = False # To ensure only one trade per day
        self.last_trade_day = -1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_trade += 1

        closes = ctx.closes(self.lookback_period * 2) # Need more history for trend
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset daily trade flag at the start of a new day (UTC 00:00)
        if current_day != self.last_trade_day:
            self.daily_trade_flag = False
            self.last_trade_day = current_day

        # Define the late US session (18:00 UTC to 23:59 UTC)
        is_late_us_session = 18 <= current_hour < 24

        # Exit any open position if we are outside the session or at the end
        if ctx.has_position() and not is_late_us_session:
            if ctx.position_direction() == "long":
                reason = "session_end_exit_long"
            else:
                reason = "session_end_exit_short"
            return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "current_hour": current_hour})

        if not is_late_us_session:
            return None

        # Cooldown check
        if self.bars_since_last_trade < self.entry_cooldown_bars:
            return None

        # Only one trade per session/day
        if self.daily_trade_flag:
            return None

        # Calculate a longer-term trend filter using SMA
        long_sma_period = 40 # Roughly 1.5 days
        long_sma = self._sma(closes, long_sma_period)
        if long_sma is None:
            return None

        # Do not fade strong trends (price well above/below long SMA)
        # Use a percentage deviation for trend strength
        trend_deviation_pct = 0.005 # 0.5% deviation
        if ctx.bar.close > long_sma * (1 + trend_deviation_pct) or \
           ctx.bar.close < long_sma * (1 - trend_deviation_pct):
            # If price is strongly trending, do not fade
            return None

        # Regime filter: avoid trading in crisis or extreme volatility
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Funding rate filter: avoid fading against strong perp funding bias
        # Positive funding rate means longs pay shorts (bullish sentiment), negative for bearish
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        # If funding is extremely positive and we're looking to short, or vice versa
        if (funding_rate > 0.0002 and ctx.bar.close > long_sma) or \
           (funding_rate < -0.0002 and ctx.bar.close < long_sma):
            # Strong funding rate confirms trend, avoid fading
            return None

        # Find the session's starting price for midpoint calculation
        # We need bars from the beginning of the current session (18:00 UTC)
        session_closes = []
        session_highs = []
        session_lows = []
        for i in range(self.max_session_bars):
            if ctx.bar.timestamp.replace(hour=current_hour - i, minute=0, second=0, microsecond=0).hour == 18:
                # This is a bit of a hack for simplicity, ideally we'd track actual session start
                # For this simple example, we assume the session started 'i' bars ago at 18:00
                # A more robust solution would track the exact bar of session start
                if len(closes) > i and len(ctx.highs(self.lookback_period + i)) > i and len(ctx.lows(self.lookback_period + i)) > i:
                    session_closes.append(closes[-1-i])
                    session_highs.append(ctx.highs(self.lookback_period + i)[-1-i])
                    session_lows.append(ctx.lows(self.lookback_period + i)[-1-i])
                break # Found the start of the session, no need to go further back

        if not session_closes: # Not enough bars into the session yet
            return None

        # Calculate session range and midpoint
        session_open = session_closes[-1] # First close of the session
        session_max_high = max(session_highs)
        session_min_low = min(session_lows)
        
        session_midpoint = (session_max_high + session_min_low) / 2

        # Check for stretched move and fade opportunity
        # Price is far from the session midpoint and we fade it
        deviation_from_midpoint_pct = (ctx.bar.close - session_midpoint) / session_midpoint

        signal_confidence = 0.0
        signal_direction = "hold"
        reason = "no_signal"

        if not ctx.has_position():
            # Fade long if price is significantly above midpoint
            if deviation_from_midpoint_pct > self.entry_threshold_pct:
                signal_direction = "short"
                signal_confidence = min(0.8, abs(deviation_from_midpoint_pct) * 100) # Confidence scales with deviation
                reason = "late_us_session_fade_short"
            # Fade short if price is significantly below midpoint
            elif deviation_from_midpoint_pct < -self.entry_threshold_pct:
                signal_direction = "long"
                signal_confidence = min(0.8, abs(deviation_from_midpoint_pct) * 100)
                reason = "late_us_session_fade_long"

            if signal_direction != "hold":
                self.bars_since_last_trade = 0
                self.daily_trade_flag = True
                return ctx.signal(
                    signal_direction,
                    confidence=signal_confidence,
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "close": ctx.bar.close,
                        "session_midpoint": session_midpoint,
                        "deviation_pct": deviation_from_midpoint_pct,
                        "long_sma": long_sma,
                        "funding_rate": funding_rate
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset bars_since_last_trade on position close to allow new entries after cooldown
        self.bars_since_last_trade = 0