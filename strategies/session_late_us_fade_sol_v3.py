from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeSOL(Strategy):
    METADATA = {
        "name": "SessionFadeSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18
        self.session_end_hour = 24 # UTC 23:59:59
        self.session_midpoint_period = 6 # 6 bars for a 6-hour session
        self.max_session_move_bars = 4 # How many bars to look for session stretch
        self.min_stretch_pct = 0.5 # Minimum percentage move to consider stretched
        self.cooldown_bars = 6 # Cooldown after a trade (6 hours)
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow first trade

        # Thresholds for fading
        self.rsi_fade_threshold_upper = 70
        self.rsi_fade_threshold_lower = 30
        self.rsi_period = 14

        # Trend filter
        self.ema_long_period = 50
        self.ema_short_period = 20

        self.last_session_day = -1 # To ensure only one trade per session/day

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_gain is None or avg_loss is None:
            return None

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero, handle flat market
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None
        
        # Only trade once per session/day
        if current_day == self.last_session_day and current_hour >= self.session_start_hour:
            return None

        # Exit existing position near session end or if holding too long
        if ctx.has_position():
            # Exit before UTC 0
            if current_hour == 23 and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "position_direction": ctx.position_direction(),
                })
            # Exit if too much time passed (e.g. 4 hours)
            if ctx.bar_index - self.last_trade_bar_index > self.METADATA["declared_hold_seconds"] / 3600 + 1:
                 return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "max_hold_time_exit",
                    "bars_held": ctx.bar_index - self.last_trade_bar_index,
                })

        # Entry logic: Late US Session (UTC 18-24)
        if self.session_start_hour <= current_hour < self.session_end_hour:

            # Only consider entry if not already in a position
            if ctx.has_position():
                return None

            # Trend filter: Do not fade strong confirmed trends
            ema_short = self._ema(closes, self.ema_short_period)
            ema_long = self._ema(closes, self.ema_long_period)
            if ema_short is None or ema_long is None:
                return None

            # Crisis regime filter
            if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
                return None

            # Check for stretched session move and stall
            # Find the start of the session for the current day
            session_opens = []
            session_highs = []
            session_lows = []
            session_closes = []
            
            # Collect data for the current session (from session_start_hour up to current_hour)
            # Iterate backwards to find the session start
            session_data_found = 0
            for i in range(1, len(closes) + 1):
                bar_timestamp = ctx.bar.timestamp.replace(hour=current_hour - (i-1), minute=0, second=0, microsecond=0)
                # This is a simplification; a more robust way would be to iterate through actual timestamps
                # and check if they fall within the current session's day and hours.
                # For this strategy, we assume we are looking back within the current session's bars.
                
                # Simple approach: look back up to max_session_move_bars within the current session
                if current_hour - (i-1) < self.session_start_hour:
                    break # Gone past the start of the session
                
                session_opens.append(ctx.opens(i)[0])
                session_highs.append(ctx.highs(i)[0])
                session_lows.append(ctx.lows(i)[0])
                session_closes.append(ctx.closes(i)[0])
                session_data_found += 1
            
            if session_data_found < self.max_session_move_bars:
                return None # Not enough session data yet

            # Reverse the lists to have oldest first
            session_opens.reverse()
            session_highs.reverse()
            session_lows.reverse()
            session_closes.reverse()

            session_start_price = session_opens[0]
            current_price = ctx.bar.close

            session_move_pct = (current_price - session_start_price) / session_start_price * 100.0

            # Check for stall: last bar's move is small compared to overall session move
            # Or, current bar is a reversal candle
            last_bar_range = ctx.bar.high - ctx.bar.low
            avg_bar_range_session = sum([h-l for h,l in zip(session_highs, session_lows)]) / len(session_highs)

            # Condition 1: Session is stretched
            is_stretched_up = session_move_pct > self.min_stretch_pct
            is_stretched_down = session_move_pct < -self.min_stretch_pct

            # Condition 2: Price is stalling or reversing (e.g., small body after a large move, or RSI extreme)
            current_rsi = self._rsi(closes, self.rsi_period)
            if current_rsi is None:
                return None

            signal_direction = None
            fade_reason = ""
            confidence = 0.65

            # Fade long if stretched up and stalling
            if is_stretched_up and current_rsi >= self.rsi_fade_threshold_upper:
                # Check for trend: only fade if not in a strong uptrend
                if ema_short < ema_long: # Price below longer EMA, or very close
                    signal_direction = "short"
                    fade_reason = "session_stretched_up_rsi_overbought_fade"
                    confidence = 0.75 if current_rsi >= 75 else 0.65
                elif abs(ema_short - ema_long) / ema_long < 0.001: # Neutral trend
                     signal_direction = "short"
                     fade_reason = "session_stretched_up_rsi_overbought_fade_neutral_trend"
                     confidence = 0.7

            # Fade short if stretched down and stalling
            elif is_stretched_down and current_rsi <= self.rsi_fade_threshold_lower:
                # Check for trend: only fade if not in a strong downtrend
                if ema_short > ema_long: # Price above longer EMA, or very close
                    signal_direction = "long"
                    fade_reason = "session_stretched_down_rsi_oversold_fade"
                    confidence = 0.75 if current_rsi <= 25 else 0.65
                elif abs(ema_short - ema_long) / ema_long < 0.001: # Neutral trend
                    signal_direction = "long"
                    fade_reason = "session_stretched_down_rsi_oversold_fade_neutral_trend"
                    confidence = 0.7

            if signal_direction:
                self.last_trade_bar_index = ctx.bar_index
                self.last_session_day = current_day
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": fade_reason,
                        "current_hour": current_hour,
                        "session_move_pct": round(session_move_pct, 2),
                        "current_rsi": round(current_rsi, 2),
                        "ema_short": round(ema_short, 2),
                        "ema_long": round(ema_long, 2),
                        "price": ctx.bar.close
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already update last_trade_bar_index on signal, but this can be used for adaptive logic
        # For example, if position["return_bps"] is very negative, increase cooldown
        pass