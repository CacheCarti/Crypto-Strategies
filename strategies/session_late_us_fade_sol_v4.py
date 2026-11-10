from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionFade(Strategy):
    METADATA = {
        "name": "SOLUSDC Late US Session Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Max 3 hours, but typically shorter
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.session_length_bars = 6 # 18:00 UTC to 00:00 UTC
        self.min_session_range_mult = 1.5 # Session range must be X times ATR
        self.entry_cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.entry_cooldown_bars

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

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Calculate initial averages
        avg_gain = sum(gains[-(period):]) / period
        avg_loss = sum(losses[-(period):]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        current_atr = self._atr(highs, lows, closes)

        if fast_ema is None or slow_ema is None or rsi is None or current_atr is None:
            return None

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Stand down in crisis or strong confirmed trends
        if regime == "CRISIS" or regime == "MELTDOWN" or ctx.regime == "crisis":
            return None
        if trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None # Avoid fading strong trends

        # Only trade if not currently in a position
        if ctx.has_position():
            # Check if it's time to exit before session close (UTC 0)
            if current_hour >= 23 or (current_hour == 0 and ctx.bar.timestamp.minute == 0):
                if ctx.position_direction() == "long" or ctx.position_direction() == "short":
                    self.last_trade_bar = ctx.bar_index # Reset cooldown
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                    })
            return None # Hold position if not time to exit

        # --- Entry Logic: Late US Session Fade (UTC 18-24) ---
        # Look for entry between 18:00 UTC and 22:00 UTC (gives 2-3 bars to exit)
        if current_hour >= 18 and current_hour < 22:
            # Get session data
            session_closes = ctx.closes(self.session_length_bars)
            session_highs = ctx.highs(self.session_length_bars)
            session_lows = ctx.lows(self.session_length_bars)

            if len(session_closes) < self.session_length_bars:
                return None

            session_open = session_closes[0]
            session_high = max(session_highs)
            session_low = min(session_lows)
            session_range = session_high - session_low
            session_midpoint = (session_high + session_low) / 2.0

            # Condition 1: Session must be unusually stretched
            # Compare session range to average true range (ATR)
            if session_range < current_atr * self.min_session_range_mult:
                return None # Not stretched enough

            # Condition 2: Price has moved significantly from session open
            move_from_open_pct = (ctx.bar.close - session_open) / session_open * 100

            # Condition 3: Price is at an extreme of the session range
            # Condition 4: RSI is overbought/oversold, indicating exhaustion
            
            signal_direction = None
            confidence = 0.0
            reason = ""

            # Attempt to fade a strong upward move
            if ctx.bar.close > session_midpoint * 1.005 and \
               ctx.bar.close > session_open * 1.005 and \
               rsi > 70 and \
               fast_ema > slow_ema: # Ensure we are in an uptrend, fading only the excess
                
                # Further filter: price is near session high but showing signs of stalling
                if ctx.bar.close > session_high * 0.99: # Close is near session high
                    signal_direction = "short"
                    confidence = min(0.7 + (rsi - 70) * 0.01, 0.9) # Higher RSI = higher confidence
                    reason = "late_us_session_fade_overbought_uptrend_stall"

            # Attempt to fade a strong downward move
            elif ctx.bar.close < session_midpoint * 0.995 and \
                 ctx.bar.close < session_open * 0.995 and \
                 rsi < 30 and \
                 fast_ema < slow_ema: # Ensure we are in a downtrend, fading only the excess
                
                # Further filter: price is near session low but showing signs of stalling
                if ctx.bar.close < session_low * 1.01: # Close is near session low
                    signal_direction = "long"
                    confidence = min(0.7 + (30 - rsi) * 0.01, 0.9) # Lower RSI = higher confidence
                    reason = "late_us_session_fade_oversold_downtrend_stall"
            
            if signal_direction:
                self.last_trade_bar = ctx.bar_index # Reset cooldown
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "session_open": session_open,
                        "session_high": session_high,
                        "session_low": session_low,
                        "session_midpoint": session_midpoint,
                        "session_range": session_range,
                        "atr": current_atr,
                        "rsi": rsi,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "fear_greed_index": fear_greed_index,
                        "funding_rate": funding_rate,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for debugging or adaptive logic
        # For example, could adjust confidence or cooldown based on recent performance
        pass