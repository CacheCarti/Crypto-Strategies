from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionAsiaDriftETH(Strategy):
    METADATA = {
        "name": "SessionAsiaDriftETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,  # Need enough for ATR and median range
        "required_features": [
            "fear_greed_index",
            "eth_return_pct",
        ],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.max_lookback = 48  # For ATR and median range
        self.min_session_move_pct = 0.2  # Minimum percentage move for a decisive session
        self.max_session_move_pct = 1.5  # Maximum percentage move to avoid exhausted moves
        self.median_range_multiplier = 1.0  # Session move must exceed this multiple of median range
        self.cooldown_bars = 5  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow immediate trading
        self.session_entry_hour_utc = 8 # Enter after Asia session ends
        self.session_exit_hour_utc = 8 # Exit before next Asia session
        self.last_session_day = -1 # Track last day to ensure one entry per day

        # Parameters for regime filtering
        self.fear_greed_threshold_bull = 55 # Only long if fear/greed is neutral to greedy
        self.fear_greed_threshold_bear = 45 # Only short if fear/greed is neutral to fearful

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period: # Ensure enough TR values for the sum
            return None
        return sum(trs[-period:]) / period

    def _median(self, values):
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) / 2
        else:
            return sorted_values[mid]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.max_lookback)
        highs = ctx.highs(self.max_lookback)
        lows = ctx.lows(self.max_lookback)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour_utc = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Exit existing position if it's the exit hour or before a new session
        if ctx.has_position() and current_hour_utc == self.session_exit_hour_utc:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_exit_time", "current_hour": current_hour_utc})

        # Only consider new entries after the Asia session, once per day
        if current_hour_utc == self.session_entry_hour_utc and current_day != self.last_session_day:
            self.last_session_day = current_day

            # Define Asia session (UTC 0-8, so we look at bars from 0 to 7)
            # Need at least 8 bars for the full session
            if ctx.bar_index < 8: # Not enough bars to form a session yet
                return None

            # Get closes for the past 8 hours (UTC 0 to 7)
            asia_session_closes = ctx.closes(8)
            asia_session_opens = ctx.opens(8)
            asia_session_highs = ctx.highs(8)
            asia_session_lows = ctx.lows(8)

            if len(asia_session_closes) < 8:
                return None

            session_open = asia_session_opens[0]
            session_close = asia_session_closes[-1]
            session_high = max(asia_session_highs)
            session_low = min(asia_session_lows)

            session_range = session_high - session_low
            session_move = session_close - session_open
            session_move_pct = (session_move / session_open) * 100 if session_open != 0 else 0

            # Calculate median true range for comparison
            # Need a longer period for ATR to be stable
            atr_period = 20
            atr_val = self._atr(highs, lows, closes, atr_period)
            if atr_val is None:
                return None

            # Calculate median daily range (approx using ATR for now, or could find true daily ranges)
            # For 1-hour bars, ATR is already a good proxy for typical hourly range.
            # Median range of 8-hour sessions can be approximated by 8 * ATR.
            median_session_range = atr_val * 8 # Approximate median range for an 8-hour session

            # Check for decisive but not exhausted move
            is_decisive_move = abs(session_move_pct) >= self.min_session_move_pct
            is_not_exhausted = abs(session_move_pct) <= self.max_session_move_pct
            is_significant_range = session_range >= median_session_range * self.median_range_multiplier

            # Avoid single-bar spikes: check if the move was distributed over the session
            # For simplicity, we check if the last bar's move wasn't disproportionately large
            last_bar_move = asia_session_closes[-1] - asia_session_opens[-1]
            if session_move != 0 and abs(last_bar_move / session_move) > 0.7: # If last bar accounts for >70% of session move
                return None # Likely a spike, skip

            # Regime filtering
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            market_regime = ctx.market.get("regime", "NORMAL")
            trend_regime = ctx.market.get("trend_regime", "neutral")

            confidence = 0.0
            signal_direction = None
            reason = ""

            # Long entry condition
            if session_move > 0 and is_decisive_move and is_not_exhausted and is_significant_range:
                # Require positive ETH return pct and favorable F&G / trend
                eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
                if eth_return_pct > -0.5 and fear_greed_index >= self.fear_greed_threshold_bull and trend_regime in ["bull", "neutral"] and market_regime not in ["CRISIS", "MELTDOWN"]:
                    signal_direction = "long"
                    confidence = 0.6 + (session_move_pct / self.max_session_move_pct) * 0.3 # Scale confidence
                    reason = "asia_session_drift_long"

            # Short entry condition
            elif session_move < 0 and is_decisive_move and is_not_exhausted and is_significant_range:
                # Require negative ETH return pct and unfavorable F&G / trend
                eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
                if eth_return_pct < 0.5 and fear_greed_index <= self.fear_greed_threshold_bear and trend_regime in ["bear", "neutral"] and market_regime not in ["CRISIS", "MELTDOWN"]:
                    signal_direction = "short"
                    confidence = 0.6 + (abs(session_move_pct) / self.max_session_move_pct) * 0.3 # Scale confidence
                    reason = "asia_session_drift_short"

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=min(1.0, confidence), # Cap confidence at 1.0
                    metadata={
                        "reason": reason,
                        "session_move_pct": session_move_pct,
                        "session_range": session_range,
                        "median_session_range_proxy": median_session_range,
                        "current_atr": atr_val,
                        "fear_greed_index": fear_greed_index,
                        "market_regime": market_regime,
                        "trend_regime": trend_regime,
                        "eth_return_pct": eth_return_pct,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close to allow new entries after cooldown
        self.last_trade_bar = ctx.bar_index