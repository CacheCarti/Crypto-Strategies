from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightFade(Strategy):
    METADATA = {
        "name": "Overnight Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour holds
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 0
        self.session_end_hour = 6 # UTC 0-6 is the overnight window
        self.fade_period = 6 # Look back 6 hours for session range
        self.sma_period = 20 # For trend filtering
        self.atr_period = 14
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        self.last_session_day = -1 # To ensure only one trade per session day

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Trend filter: Don't trade against strong trend
        sma_val = self._sma(closes, self.sma_period)
        if sma_val is None:
            return None

        # Volatility filter
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None:
            return None
        
        # Regime filter: Avoid crisis
        if ctx.market.get("regime", "NORMAL") == "CRISIS":
            return None
        
        # Sentiment filter: Avoid trading against extreme fear/greed if fading
        fear_greed = ctx.features.get("fear_greed_index", 50)


        # Check if we are in the session end hour (UTC 6) and haven't traded today
        if current_hour == self.session_end_hour and current_day != self.last_session_day:
            # Look back over the session period (0-6 UTC)
            session_closes = ctx.closes(self.fade_period + 1) # Need one more for diff
            session_highs = ctx.highs(self.fade_period + 1)
            session_lows = ctx.lows(self.fade_period + 1)
            
            if len(session_closes) < self.fade_period + 1:
                return None

            # Calculate session range and midpoint
            session_high = max(session_highs[-(self.fade_period+1):-1]) # Exclude current bar
            session_low = min(session_lows[-(self.fade_period+1):-1]) # Exclude current bar
            session_midpoint = (session_high + session_low) / 2.0

            # Calculate the "stretch" of the move relative to ATR
            # We are looking for a significant move from the session open (or earlier close)
            # to the current close within the session.
            # Use the close at the start of the session (hour 0) as reference
            # For simplicity, let's assume the session_closes[0] is roughly the start of the session.
            session_start_price = session_closes[0]
            session_move = ctx.bar.close - session_start_price
            
            # If the session move is greater than 1.5x ATR, consider it stretched
            stretch_threshold = 1.5 * atr_val

            # LONG Signal: Price has moved significantly down, and we expect a bounce towards midpoint
            if session_move < -stretch_threshold and ctx.bar.close < session_midpoint:
                # Filter: Don't long if current price is far below SMA (strong downtrend)
                if ctx.bar.close < sma_val * 0.99: # 1% below SMA
                    return None
                # Filter: If extreme greed, shorting might be better, but we are fading a down move
                if fear_greed > 75: # Counter-intuitive to long if market is extremely greedy, implies a top
                    return None
                
                self.last_signal_bar = ctx.bar_index
                self.last_session_day = current_day
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overnight_down_fade_to_midpoint",
                        "session_move_pct": (session_move / session_start_price) * 100,
                        "session_midpoint": session_midpoint,
                        "current_price": ctx.bar.close,
                        "atr": atr_val,
                        "fear_greed": fear_greed
                    }
                )

            # SHORT Signal: Price has moved significantly up, and we expect a retracement towards midpoint
            elif session_move > stretch_threshold and ctx.bar.close > session_midpoint:
                # Filter: Don't short if current price is far above SMA (strong uptrend)
                if ctx.bar.close > sma_val * 1.01: # 1% above SMA
                    return None
                # Filter: If extreme fear, longing might be better, but we are fading an up move
                if fear_greed < 25: # Counter-intuitive to short if market is extremely fearful, implies a bottom
                    return None

                self.last_signal_bar = ctx.bar_index
                self.last_session_day = current_day
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overnight_up_fade_to_midpoint",
                        "session_move_pct": (session_move / session_start_price) * 100,
                        "session_midpoint": session_midpoint,
                        "current_price": ctx.bar.close,
                        "atr": atr_val,
                        "fear_greed": fear_greed
                    }
                )
        
        # Exit logic: If we have a position and it's approaching the next US session (e.g., UTC 13)
        # or if it moves against us significantly beyond typical noise (handled by SL)
        if ctx.has_position() and current_hour == 13: # Close before US session starts
            position = ctx.position
            if position:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "entry_price": position["entry_price"],
                        "current_price": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal bar to enforce cooldown after any position close
        self.last_signal_bar = ctx.bar_index
        pass