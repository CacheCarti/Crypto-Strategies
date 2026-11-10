from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLateUsSessionFade(Strategy):
    METADATA = {
        "name": "BTC Late US Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 50,  # Need enough bars for EMA and session data
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 18  # UTC 18:00 (late US session start)
        self.session_end_hour = 23    # UTC 23:00 (trade until end of this hour)
        self.exit_hour = 0            # Exit before UTC 0:00

        self.ema_period = 20
        self.rsi_period = 14
        self.session_bars = 0
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = 0.0
        self.traded_this_session = False
        self.last_signal_bar_index = -self.METADATA["warmup_bars"] # Initialize to allow trading after warmup

        self.cooldown_bars = 6 # Wait 6 bars (6 hours) after a trade before new entry

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
        
        # Calculate initial averages
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero, handle flat
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Daily reset for session tracking
        if current_hour == self.exit_hour and self.traded_this_session:
            self.traded_this_session = False # Reset for new day
            self.session_bars = 0
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open_price = 0.0
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Update session data
        if current_hour >= self.session_start_hour:
            if self.session_bars == 0: # First bar of the session
                self.session_open_price = ctx.bar.open
                self.session_high = ctx.bar.high
                self.session_low = ctx.bar.low
            else:
                self.session_high = max(self.session_high, ctx.bar.high)
                self.session_low = min(self.session_low, ctx.bar.low)
            self.session_bars += 1
        else: # Outside session hours, reset session tracking
            self.session_bars = 0
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open_price = 0.0
            self.traded_this_session = False # Also reset if we are outside session entirely

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        # Calculate indicators
        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema is None or rsi is None:
            return None

        # Fade logic within the late US session
        if self.session_start_hour <= current_hour <= self.session_end_hour and not self.traded_this_session:

            # Regime filters
            regime = ctx.market.get("regime", "NORMAL")
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            
            # Do not fade in strong crisis or very high volatility
            if regime in ["CRISIS", "MELTDOWN"] or ctx.market.get("crisis_score", 0.0) > 0.5:
                return None
            
            # Avoid fading extremely strong trends (e.g. fear & greed extreme)
            if fear_greed_index >= 80 or fear_greed_index <= 20: # Extreme greed/fear means strong trend
                return None

            # Calculate session extension
            session_range = self.session_high - self.session_low
            midpoint = (self.session_high + self.session_low) / 2

            # Entry conditions
            # Look for price far from midpoint, indicating a stretched move
            # And an overbought/oversold RSI condition
            # And price relative to EMA (not a strong trend)

            # Long signal: Stretched down, fading back up
            if (current_close < self.session_low + session_range * 0.15 and # Price near session low
                current_close < self.session_open_price * 0.995 and # Price below session open
                rsi < 35 and # Oversold
                current_close < ema * 1.005 # Not strongly above EMA
            ):
                if not ctx.has_position():
                    self.traded_this_session = True
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "late_us_session_fade_long",
                            "current_hour": current_hour,
                            "session_open": self.session_open_price,
                            "session_low": self.session_low,
                            "current_close": current_close,
                            "rsi": rsi,
                            "ema": ema,
                            "regime": regime,
                            "fear_greed_index": fear_greed_index
                        }
                    )
            
            # Short signal: Stretched up, fading back down
            elif (current_close > self.session_high - session_range * 0.15 and # Price near session high
                  current_close > self.session_open_price * 1.005 and # Price above session open
                  rsi > 65 and # Overbought
                  current_close > ema * 0.995 # Not strongly below EMA
            ):
                if not ctx.has_position():
                    self.traded_this_session = True
                    self.last_signal_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "late_us_session_fade_short",
                            "current_hour": current_hour,
                            "session_open": self.session_open_price,
                            "session_high": self.session_high,
                            "current_close": current_close,
                            "rsi": rsi,
                            "ema": ema,
                            "regime": regime,
                            "fear_greed_index": fear_greed_index
                        }
                    )
        
        # If we have a position, hold it until session end or stop/take profit
        if ctx.has_position():
            return ctx.signal("hold", confidence=0.0) # Conf 0 as we're just holding

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_this_session flag if position closed for any reason
        # This allows re-entry within the same session if conditions re-align,
        # but the cooldown logic will still apply.
        # self.traded_this_session = False # Re-evaluate if this is desired behavior.
        # For this strategy, we want to trade only once per session, so keep traded_this_session = True after entry.
        # The daily reset at self.exit_hour handles it.
        pass