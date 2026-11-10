from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionFade(Strategy):
    METADATA = {
        "name": "SOL Session Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Intended to hold for up to 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18  # UTC 18:00 (late US session)
        self.session_end_hour = 23    # UTC 23:00 (end of late US session)
        self.exit_hour = 0            # UTC 00:00 (next day start)
        self.lookback_period = 20     # For session range, volatility
        self.fade_threshold_pct = 0.5 # Percentage of session range to consider "stretched"
        self.fade_reversion_pct = 0.25 # How far back into the session range to target
        self.cooldown_bars = 4        # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Indicators for trend filtering
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.rsi_period = 14
        self.min_bars_for_indicators = max(self.lookback_period, self.ema_long_period, self.rsi_period)


    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = self._sma(values[:period], period) # Use SMA for initial EMA
        if ema is None: return None
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.min_bars_for_indicators + 1)
        highs = ctx.highs(self.min_bars_for_indicators)
        lows = ctx.lows(self.min_bars_for_indicators)

        if len(closes) < self.min_bars_for_indicators + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Determine if we are in the active session for entry
        is_entry_hour = self.session_start_hour <= current_hour <= self.session_end_hour

        # Exit logic
        if ctx.has_position():
            # Exit if past exit hour
            if current_hour >= self.exit_hour and current_hour < self.session_start_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            
            # Exit if market regime turns crisis
            if ctx.market.get("regime", "NORMAL") == "CRISIS":
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "market_crisis_exit", "regime": ctx.market.get("regime")})

            # Exit if trend confirmation reverses strongly
            ema_short = self._ema(closes, self.ema_short_period)
            ema_long = self._ema(closes, self.ema_long_period)
            if ema_short is None or ema_long is None: return None

            if ctx.position_direction() == "long" and ema_short < ema_long:
                 return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_trend_reversal_exit", "ema_short": ema_short, "ema_long": ema_long})
            if ctx.position_direction() == "short" and ema_short > ema_long:
                 return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_trend_reversal_exit", "ema_short": ema_short, "ema_long": ema_long})


            # If position exists and it's not time to exit, just hold
            return None

        # Entry logic - only if no position and within entry hours
        if not is_entry_hour:
            return None

        # Regime filters
        if ctx.market.get("regime", "NORMAL") == "CRISIS":
            return None # Do not fade in crisis
        
        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 25 or fear_greed > 75: # Avoid extreme sentiment
            return None

        # Trend filter (avoid fading strong trends)
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)
        if ema_short is None or ema_long is None: return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None: return None

        # Check if current bar is the first bar of the session or if it's the first hour after session start
        # to establish session boundaries
        if current_hour == self.session_start_hour:
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_open = ctx.bar.open
        else:
            # Update session high/low for the current session
            self.session_high = max(getattr(self, 'session_high', -math.inf), ctx.bar.high)
            self.session_low = min(getattr(self, 'session_low', math.inf), ctx.bar.low)


        # Ensure session bounds are initialized
        if not hasattr(self, 'session_high') or not hasattr(self, 'session_low') or not hasattr(self, 'session_open'):
            return None

        session_range = self.session_high - self.session_low
        if session_range == 0:
            return None

        # Calculate session midpoint
        session_midpoint = (self.session_high + self.session_low) / 2
        
        # Check if price is stretched
        is_stretched_up = (ctx.bar.close - self.session_open) / session_range > self.fade_threshold_pct
        is_stretched_down = (self.session_open - ctx.bar.close) / session_range > self.fade_threshold_pct

        # Determine fade direction
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Fade short
        if is_stretched_up and current_rsi > 60: # Overbought in stretched session
            if ema_short < ema_long * 1.002: # Not in a super strong uptrend
                signal_direction = "short"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1) # Add slight randomness to confidence
                reason = "session_stretch_fade_short"
                self.last_trade_bar = ctx.bar_index

        # Fade long
        elif is_stretched_down and current_rsi < 40: # Oversold in stretched session
            if ema_short > ema_long * 0.998: # Not in a super strong downtrend
                signal_direction = "long"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1)
                reason = "session_stretch_fade_long"
                self.last_trade_bar = ctx.bar_index

        if signal_direction:
            # Calculate dynamic TP based on reversion to session midpoint
            if signal_direction == "short":
                # Target slightly above midpoint
                target_price = session_midpoint + (session_range * self.fade_reversion_pct)
                take_profit_bps = max(self.METADATA["declared_tp_bps"], (ctx.bar.close - target_price) / ctx.bar.close * 10000)
            else: # long
                # Target slightly below midpoint
                target_price = session_midpoint - (session_range * self.fade_reversion_pct)
                take_profit_bps = max(self.METADATA["declared_tp_bps"], (target_price - ctx.bar.close) / ctx.bar.close * 10000)

            # Ensure TP is positive
            take_profit_bps = max(50.0, take_profit_bps)

            return ctx.signal(
                signal_direction,
                confidence=min(1.0, max(0.5, confidence)),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=take_profit_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_open": self.session_open,
                    "session_high": self.session_high,
                    "session_low": self.session_low,
                    "session_midpoint": session_midpoint,
                    "current_price": ctx.bar.close,
                    "is_stretched_up": is_stretched_up,
                    "is_stretched_down": is_stretched_down,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "rsi": current_rsi,
                    "fear_greed": fear_greed,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic based on trade outcome
        # For now, just logging
        pass