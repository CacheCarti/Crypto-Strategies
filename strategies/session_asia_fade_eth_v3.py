from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthAsiaSessionFade(Strategy):
    METADATA = {
        "name": "ETH Asia Session Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-hour bar, so up to 8 hours max
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 30 # For trend detection
        self.short_period = 10 # For session range
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars
        self.session_midpoint = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_start_bar_index = -1
        self.has_traded_this_session = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + 1)
        highs = ctx.highs(self.short_period)
        lows = ctx.lows(self.short_period)

        if len(closes) < self.long_period + 1 or len(highs) < self.short_period or len(lows) < self.short_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Calculate long-term EMA for trend filtering
        long_ema = self._ema(closes, self.long_period)
        if long_ema is None:
            return None

        # Asia Session (UTC 0-8) logic
        is_asia_session = 0 <= current_hour < 8

        # Reset session data at the start of a new Asia session
        if current_hour == 0 and ctx.bar_index != self.session_start_bar_index:
            self.session_midpoint = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_start_bar_index = ctx.bar_index
            self.has_traded_this_session = False

        if is_asia_session:
            # Update session high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

            if self.session_midpoint is None:
                # Initialize session midpoint with the first close of the session
                self.session_midpoint = ctx.bar.close
            else:
                # Update session midpoint with a simple average of high/low or EMA of closes
                # For simplicity, let's use the average of the current session's high/low
                self.session_midpoint = (self.session_high + self.session_low) / 2

        # Exit logic if position is open and session is ending or midpoint is hit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Exit before Asia session ends (UTC 8)
            if current_hour >= 8:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "asia_session_end_exit",
                    "current_hour": current_hour,
                    "entry_price": entry_price,
                    "current_price": current_close
                })

            # Adaptive stop: if price returns to midpoint, exit
            if self.session_midpoint is not None:
                if pos_dir == "long" and current_close <= self.session_midpoint:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "session_midpoint_reversal_stop_long",
                        "session_midpoint": self.session_midpoint,
                        "entry_price": entry_price,
                        "current_price": current_close
                    })
                elif pos_dir == "short" and current_close >= self.session_midpoint:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "session_midpoint_reversal_stop_short",
                        "session_midpoint": self.session_midpoint,
                        "entry_price": entry_price,
                        "current_price": current_close
                    })
            return None # Hold position if no exit condition met

        # Entry logic
        if not is_asia_session or self.has_traded_this_session or (ctx.bar_index - self.last_signal_bar) < self.cooldown_bars:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Avoid trading during extreme fear/greed or crisis regimes
        if fear_greed < 20 or fear_greed > 80 or market_regime in ["CRISIS", "MELTDOWN"]:
            return None
        # Avoid fading strong trends
        if (trend_regime == "bull" and eth_return_pct > 1.0) or (trend_regime == "bear" and eth_return_pct < -1.0):
            return None

        # Check for unusual session stretch and stall
        if self.session_midpoint is not None:
            session_range = self.session_high - self.session_low
            if session_range == 0:
                return None

            # Calculate current price deviation from midpoint as a percentage of session range
            deviation_from_mid = (current_close - self.session_midpoint) / session_range
            
            # Look for "stretched" conditions: price far from midpoint
            stretch_threshold = 0.35 # e.g., 35% of session range away from midpoint

            # Look for a "stall": price not moving much or reversing
            # Using current bar vs previous bar, or simple SMA of last few closes
            short_sma = self._sma(closes[-self.short_period:], self.short_period)
            if short_sma is None:
                return None

            # Condition for fading:
            # 1. Price is stretched significantly from the midpoint
            # 2. Short-term momentum (current close vs short_sma) is showing a slight reversal or stall
            # 3. Overall trend is not strongly against the fade
            
            signal_confidence = 0.0
            signal_direction = "hold"
            reason = ""

            # Long entry: price stretched low, stalling/reversing up, and not strong downtrend
            if deviation_from_mid < -stretch_threshold and \
               current_close > short_sma and \
               current_close > long_ema * 0.995: # Not too far below long EMA
                signal_direction = "long"
                signal_confidence = 0.75
                reason = "asia_session_fade_long_stretched_low_stalling_up"
            
            # Short entry: price stretched high, stalling/reversing down, and not strong uptrend
            elif deviation_from_mid > stretch_threshold and \
                 current_close < short_sma and \
                 current_close < long_ema * 1.005: # Not too far above long EMA
                signal_direction = "short"
                signal_confidence = 0.75
                reason = "asia_session_fade_short_stretched_high_stalling_down"

            if signal_direction != "hold":
                self.last_signal_bar = ctx.bar_index
                self.has_traded_this_session = True
                return ctx.signal(
                    signal_direction,
                    confidence=signal_confidence,
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "session_midpoint": self.session_midpoint,
                        "current_close": current_close,
                        "deviation_from_mid": deviation_from_mid,
                        "session_range": session_range,
                        "short_sma": short_sma,
                        "long_ema": long_ema,
                        "fear_greed": fear_greed,
                        "eth_return_pct": eth_return_pct
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown for next entry
        self.last_signal_bar = ctx.bar_index
        # You could also use this to adapt `stretch_threshold` or `confidence` based on recent performance
        pass