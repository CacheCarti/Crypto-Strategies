from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionFade(Strategy):
    METADATA = {
        "name": "USSessionFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours, within session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00
        self.us_session_end_hour = 21    # UTC 21:00
        self.session_length_hours = self.us_session_end_hour - self.us_session_start_hour
        self.max_session_move_bps = 250 # Max move before considering fade
        self.fade_confidence_threshold = 0.6
        self.cooldown_bars = 4 # Wait 4 bars (4 hours) after a trade
        self.last_signal_bar_index = -self.cooldown_bars

        # Indicators for trend and volatility filtering
        self.ema_period_slow = 20
        self.atr_period = 14

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # --- Trend and Regime Filters ---
        # Don't fade strong confirmed trends or crisis regimes
        slow_ema = self._ema(closes, self.ema_period_slow)
        if slow_ema is None:
            return None

        # Avoid trading during extreme volatility
        current_atr = self._atr(ctx.highs(self.atr_period + 1), ctx.lows(self.atr_period + 1), closes, self.atr_period)
        if current_atr is None:
            return None
        
        # Simple volatility filter: if ATR is too high (e.g., top 10%), avoid trading
        # This is a simplification; a more robust filter would compare to historical ATR distribution
        # For now, let's assume a fixed threshold or relative to price
        atr_to_price_ratio = (current_atr / ctx.bar.close) * 10000 # in bps
        if atr_to_price_ratio > 100: # If ATR is > 100 bps, consider it high volatility
            return None

        # Filter out crisis regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        # Filter out extreme fear/greed
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        if fear_greed_index < 20 or fear_greed_index > 80:
            return None # Avoid trading during extreme sentiment

        # --- Session Logic ---
        # We only care about entering a trade once within the US session, and exiting before it ends
        
        # Determine session start for today
        # Check if we are within the US session hours
        is_us_session_active = self.us_session_start_hour <= current_hour < self.us_session_end_hour

        if not is_us_session_active:
            # If we have a position, close it before session ends (or if outside session)
            if ctx.has_position():
                if ctx.position_direction() == "long" and ctx.bar.timestamp.hour >= self.us_session_end_hour -1: # Exit 1 hour before session end
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "session_end_exit_long", "current_hour": current_hour})
                if ctx.position_direction() == "short" and ctx.bar.timestamp.hour >= self.us_session_end_hour -1:
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "session_end_exit_short", "current_hour": current_hour})
            return None

        # Entry logic: only if no position
        if ctx.has_position():
            return None

        # Find the session open price. This requires looking back to the start of the session.
        # We need to find the specific bar that marks the start of the current session.
        session_opens = []
        session_closes = []
        session_highs = []
        session_lows = []
        session_volumes = []
        
        # Iterate backwards to find the session start
        for i in range(1, self.session_length_hours + 2): # Max bars to check for session start
            if ctx.bar_index - i < 0:
                break
            
            prev_bar_hour = ctx.bar.timestamp.hour - i
            if prev_bar_hour < 0:
                prev_bar_hour += 24 # Handle hour wrapping
            
            if prev_bar_hour == self.us_session_start_hour: # Found the session start bar
                session_open_price = ctx.opens(i)[0] # This is the open of the first bar of the session
                
                # Gather all bars since session start
                for j in range(i):
                    session_opens.append(ctx.opens(j+1)[0])
                    session_closes.append(ctx.closes(j+1)[0])
                    session_highs.append(ctx.highs(j+1)[0])
                    session_lows.append(ctx.lows(j+1)[0])
                    session_volumes.append(ctx.volumes(j+1)[0])
                
                # Reverse to get chronological order
                session_opens.reverse()
                session_closes.reverse()
                session_highs.reverse()
                session_lows.reverse()
                session_volumes.reverse()
                
                break
        else:
            # Not enough history to determine session start, or not yet in session
            return None
        
        if not session_closes: # Should not happen if loop above is correct
            return None

        # Calculate session move
        current_session_move_bps = ((ctx.bar.close - session_open_price) / session_open_price) * 10000

        # Check for "stretched" move
        if abs(current_session_move_bps) > self.max_session_move_bps:
            # Check for stalling: current bar's move is small compared to overall session move
            # Or, current bar is reversing
            
            # Simple stalling/reversal check:
            # Is the current bar's direction opposite to the session's overall direction?
            # And is the current bar's range smaller than recent average?
            
            current_bar_change = ctx.bar.close - ctx.bar.open
            session_overall_direction = 1 if current_session_move_bps > 0 else -1

            # Transition detection: ensure we only enter once after a stretch and stall
            # This requires looking at the *previous* bar's state.
            # For simplicity here, we'll check if the current bar is a strong reversal candle
            # (e.g., closing significantly opposite to its open, and against the session trend)
            
            # If session is stretched up, look for short (price reversal down)
            if current_session_move_bps > self.max_session_move_bps: # Session is stretched upwards
                if current_bar_change < 0 and (abs(current_bar_change) / current_atr) > 0.5: # Current bar is down and significant
                    # Check if current price is above slow EMA (trend filter)
                    if ctx.bar.close > slow_ema: # If still strongly trending up, don't fade
                        return None
                    
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=self.fade_confidence_threshold,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "us_session_fade_stretched_up_reversal",
                            "session_open_price": session_open_price,
                            "current_session_move_bps": current_session_move_bps,
                            "current_close": ctx.bar.close,
                            "current_bar_change": current_bar_change,
                            "slow_ema": slow_ema
                        }
                    )
            
            # If session is stretched down, look for long (price reversal up)
            elif current_session_move_bps < -self.max_session_move_bps: # Session is stretched downwards
                if current_bar_change > 0 and (abs(current_bar_change) / current_atr) > 0.5: # Current bar is up and significant
                    # Check if current price is below slow EMA (trend filter)
                    if ctx.bar.close < slow_ema: # If still strongly trending down, don't fade
                        return None

                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=self.fade_confidence_threshold,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "us_session_fade_stretched_down_reversal",
                            "session_open_price": session_open_price,
                            "current_session_move_bps": current_session_move_bps,
                            "current_close": ctx.bar.close,
                            "current_bar_change": current_bar_change,
                            "slow_ema": slow_ema
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_signal_bar_index = ctx.bar_index
        # You could implement adaptive sizing here based on position["return_bps"]
        # For now, just reset the cooldown.