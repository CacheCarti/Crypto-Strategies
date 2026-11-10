from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionUSFadeBTC(Strategy):
    METADATA = {
        "name": "Session US Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2, # ~2 hours max hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20 # for ATR and Z-score
        self.cooldown_bars = 6 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.entry_bar_index = -1
        self.session_open_price = None
        self.session_high = None
        self.session_low = None
        self.session_midpoint = None
        self.fade_max_stretch_factor = 2.0 # how many multiples of ATR from session open to consider "stretched"
        self.min_stretch_from_midpoint_atr = 0.5 # minimum stretch from midpoint to fade
        self.max_fear_greed_for_long_fade = 60 # Don't fade short if greed is too high (potential for continued pump)
        self.min_fear_greed_for_short_fade = 40 # Don't fade long if fear is too high (potential for continued dump)

    def _atr(self, highs, lows, closes, period):
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

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)
        volumes = ctx.volumes(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close
        
        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # US Session: UTC 13 to 21 (9 hours)
        # Session start (UTC 13)
        if current_hour == 13 and ctx.bar.timestamp.minute == 0:
            self.session_open_price = current_price
            self.session_high = current_price
            self.session_low = current_price
            self.session_midpoint = None # Reset midpoint
            self.entry_bar_index = -1 # Reset for daily entry
            # print(f"Session start at {ctx.bar.timestamp}. Open: {self.session_open_price}")
        
        # Update session high/low
        if self.session_open_price is not None and 13 <= current_hour < 21:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_midpoint = (self.session_high + self.session_low) / 2.0
            # print(f"Current session h/l/mid: {self.session_high}/{self.session_low}/{self.session_midpoint}")


        # Trading logic only within US session (UTC 14 to 20, to allow for some session movement)
        if 14 <= current_hour < 20 and self.session_open_price is not None and self.session_midpoint is not None:
            if ctx.has_position():
                # If we have a position, don't enter another
                return None
            
            # Ensure we only enter once per session
            if self.entry_bar_index != -1 and self.entry_bar_index // 24 == ctx.bar_index // 24:
                return None

            current_atr = self._atr(highs, lows, closes, self.lookback_period)
            if current_atr is None or current_atr == 0:
                return None

            # Get market context and features
            regime = ctx.market.get("regime", "NORMAL")
            trend_regime = ctx.market.get("trend_regime", "neutral")
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

            # Avoid trading in crisis regimes or strong trends
            if regime in ["CRISIS", "MELTDOWN"]:
                return None
            if trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
                return None

            # Calculate current stretch from session open
            stretch_from_open = (current_price - self.session_open_price) / current_atr
            stretch_from_midpoint = (current_price - self.session_midpoint) / current_atr

            # Long fade (price is too low, fade back to midpoint)
            if stretch_from_open < -self.fade_max_stretch_factor and stretch_from_midpoint < -self.min_stretch_from_midpoint_atr:
                # Check for confirmation: price trying to bounce, e.g. positive z-score on recent closes
                z_score = self._zscore(closes, 5) # Short-term momentum
                if z_score is not None and z_score > 0.5: # Price is bouncing
                    # Avoid fading long if fear is too high (market might continue dumping)
                    if fear_greed_index > self.min_fear_greed_for_short_fade:
                        self.last_trade_bar = ctx.bar_index
                        self.entry_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.65,
                            metadata={
                                "reason": "us_session_long_fade",
                                "session_open": self.session_open_price,
                                "session_midpoint": self.session_midpoint,
                                "current_price": current_price,
                                "stretch_from_open_atr": stretch_from_open,
                                "stretch_from_midpoint_atr": stretch_from_midpoint,
                                "current_atr": current_atr,
                                "z_score_5bar": z_score,
                                "fear_greed": fear_greed_index
                            }
                        )
            
            # Short fade (price is too high, fade back to midpoint)
            elif stretch_from_open > self.fade_max_stretch_factor and stretch_from_midpoint > self.min_stretch_from_midpoint_atr:
                # Check for confirmation: price trying to pull back, e.g. negative z-score on recent closes
                z_score = self._zscore(closes, 5) # Short-term momentum
                if z_score is not None and z_score < -0.5: # Price is pulling back
                    # Avoid fading short if greed is too high (market might continue pumping)
                    if fear_greed_index < self.max_fear_greed_for_long_fade:
                        self.last_trade_bar = ctx.bar_index
                        self.entry_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.65,
                            metadata={
                                "reason": "us_session_short_fade",
                                "session_open": self.session_open_price,
                                "session_midpoint": self.session_midpoint,
                                "current_price": current_price,
                                "stretch_from_open_atr": stretch_from_open,
                                "stretch_from_midpoint_atr": stretch_from_midpoint,
                                "current_atr": current_atr,
                                "z_score_5bar": z_score,
                                "fear_greed": fear_greed_index
                            }
                        )

        # Exit logic: Flat position before end of session (UTC 21) or if session is ending
        if ctx.has_position() and current_hour >= 20: # Exit at UTC 20:00 (one hour before session end)
            if ctx.position_direction() == "long":
                exit_reason = "session_end_long_exit"
            else:
                exit_reason = "session_end_short_exit"
            
            self.last_trade_bar = ctx.bar_index # Cooldown after exit
            return ctx.signal(
                "flat",
                confidence=0.9,
                metadata={
                    "reason": exit_reason,
                    "current_hour": current_hour,
                    "current_price": current_price,
                    "session_open": self.session_open_price,
                    "session_midpoint": self.session_midpoint
                }
            )
            
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any session-specific variables if needed, or log for adaptive sizing.
        # For this strategy, the cooldown `self.last_trade_bar` is updated on signal.
        pass