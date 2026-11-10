from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionDriftSOL(Strategy):
    METADATA = {
        "name": "USSessionDriftSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # Aim for ~2-4 hour holds
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13
        self.us_session_end_hour = 21
        self.min_session_move_factor = 1.5 # Session move must be 1.5x median range
        self.median_range_period = 30 # For median daily range
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1
        self.session_traded_today = False
        self.last_bar_day = -1

    def _median(self, data):
        if not data:
            return 0
        sorted_data = sorted(data)
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        return sorted_data[mid]

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_range_period + 1)
        highs = ctx.highs(self.median_range_period + 1)
        lows = ctx.lows(self.median_range_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset daily flag
        if current_day != self.last_bar_day:
            self.session_traded_today = False
            self.last_bar_day = current_day

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate median true range
        recent_ranges = []
        for i in range(1, min(len(highs), self.median_range_period + 1)):
            recent_ranges.append(highs[i] - lows[i])
        median_daily_range = self._median(recent_ranges)

        if median_daily_range == 0:
            return None

        # Regime filter: only trade in normal or high_vol, avoid crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Trend filter: only take long trades in bull/neutral, short in bear/neutral
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # US session entry logic
        if current_hour == self.us_session_end_hour and not ctx.has_position() and not self.session_traded_today:
            # Analyze the US session's move
            session_opens = ctx.opens(current_hour - self.us_session_start_hour + 1)
            session_closes = ctx.closes(current_hour - self.us_session_start_hour + 1)
            session_highs = ctx.highs(current_hour - self.us_session_start_hour + 1)
            session_lows = ctx.lows(current_hour - self.us_session_start_hour + 1)

            if len(session_opens) < (self.us_session_end_hour - self.us_session_start_hour): # Not enough bars for full session
                 return None
            
            session_open_price = session_opens[0]
            session_close_price = session_closes[-1]
            session_high_price = max(session_highs)
            session_low_price = min(session_lows)

            session_move = session_close_price - session_open_price
            session_range = session_high_price - session_low_price
            
            min_decisive_move = median_daily_range * self.min_session_move_factor

            # Avoid single-bar spikes: check if the move was sustained
            # If the session range is too small relative to the move, it's a spike
            if abs(session_move) > 0.75 * session_range: # If most of the range is just the open-close, it's decisive
                pass # This is fine, we want decisive moves
            else:
                return None # Not decisive enough relative to range

            if session_move > min_decisive_move: # Decisive bullish session
                if trend_regime in ["bull", "neutral"]:
                    self.session_traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "us_session_bull_drift",
                            "session_move": session_move,
                            "min_decisive_move": min_decisive_move,
                            "median_daily_range": median_daily_range,
                            "current_regime": regime,
                            "trend_regime": trend_regime,
                        }
                    )
            elif session_move < -min_decisive_move: # Decisive bearish session
                if trend_regime in ["bear", "neutral"]:
                    self.session_traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "us_session_bear_drift",
                            "session_move": session_move,
                            "min_decisive_move": min_decisive_move,
                            "median_daily_range": median_daily_range,
                            "current_regime": regime,
                            "trend_regime": trend_regime,
                        }
                    )
        
        # Exit logic: if position open, exit before next US session starts (or at end of current session)
        if ctx.has_position() and current_hour == self.us_session_end_hour: # Exit at the end of the US session if still holding
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "session_end_exit",
                    "exit_hour": current_hour,
                    "position_direction": ctx.position_direction(),
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We set last_trade_bar on entry, but also ensure cooldown from exit
        self.last_trade_bar = ctx.bar_index
        pass