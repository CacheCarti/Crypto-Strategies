from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFadeSOL(Strategy):
    METADATA = {
        "name": "Asia Session Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold for a few hours within session
        "warmup_bars": 50,  # Need enough bars for 20-period calculations
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_bar_index = -1
        self.session_high = -1.0
        self.session_low = -1.0
        self.session_open_price = -1.0
        self.session_midpoint = -1.0
        self.traded_today = False
        self.cooldown_bars = 0
        self.long_period = 20 # For EMA and StdDev
        self.short_period = 10 # For shorter momentum check

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        # Reset session data at UTC 0 (start of Asia session)
        if current_hour == 0 and self.session_open_bar_index != ctx.bar_index:
            self.session_open_bar_index = ctx.bar_index
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_midpoint = self.session_open_price
            self.traded_today = False
            return None # Don't trade on the first bar of the session

        # Update session high/low for bars within the session (0-7 UTC)
        if 0 <= current_hour < 8:
            if self.session_open_bar_index == -1: # Catch-up if strategy started mid-session
                 self.session_open_bar_index = ctx.bar_index
                 self.session_open_price = ctx.bar.open
                 self.session_high = ctx.bar.high
                 self.session_low = ctx.bar.low
                 self.session_midpoint = self.session_open_price

            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_midpoint = (self.session_open_price + ctx.bar.close) / 2.0 # Dynamic midpoint

        # Exit existing position before session close (UTC 8) if still open
        if ctx.has_position() and current_hour == 7 and ctx.bar.timestamp.minute >= 50: # Last few minutes of UTC 7
            if ctx.position_direction() == "long":
                 return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit_long", "exit_hour": current_hour, "session_midpoint": self.session_midpoint})
            elif ctx.position_direction() == "short":
                 return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit_short", "exit_hour": current_hour, "session_midpoint": self.session_midpoint})

        # Only consider trades during Asia session (UTC 0-7) and if not already traded
        if not (0 <= current_hour < 8) or self.traded_today:
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Do not trade in crisis or strong trends
        if crisis_score > 0.5 or regime == "CRISIS" or regime == "MELTDOWN":
            return None
        if trend_regime != "neutral" and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
            return None
        if fear_greed_index < 30 or fear_greed_index > 70: # Avoid extreme sentiment
            return None

        # Calculate session range and current extension
        session_range = self.session_high - self.session_low
        if session_range == 0:
            return None

        # Price volatility/deviation
        stddev = self._stddev(closes, self.long_period)
        ema_long = self._ema(closes, self.long_period)
        ema_short = self._ema(closes, self.short_period)

        if stddev is None or ema_long is None or ema_short is None:
            return None

        # Avoid trading during low volatility
        if stddev < self._sma(ctx.closes(self.long_period * 2), self.long_period) * 0.005: # 0.5% of avg price
             return None

        # Calculate deviation from session midpoint relative to session range
        current_deviation_from_mid = ctx.bar.close - self.session_midpoint
        deviation_ratio = current_deviation_from_mid / session_range if session_range > 0 else 0

        # Define "stretched" conditions
        stretched_threshold = 0.4 # Price is 40% away from midpoint within the session range
        momentum_threshold = 0.001 # Price change relative to EMA

        signal_confidence = 0.6

        # Fading logic
        if not ctx.has_position():
            # Fade long if stretched high
            if deviation_ratio > stretched_threshold:
                # Confirm with price below short EMA (stalling)
                if ctx.bar.close < ema_short and ema_short < ema_long: # Short term momentum turning down
                    self.traded_today = True
                    self.cooldown_bars = 5 # Cooldown for 5 bars
                    return ctx.signal(
                        "short",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "asia_session_fade_high",
                            "session_high": self.session_high,
                            "session_low": self.session_low,
                            "session_midpoint": self.session_midpoint,
                            "current_price": ctx.bar.close,
                            "deviation_ratio": deviation_ratio,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "stddev": stddev,
                            "regime": regime,
                            "trend_regime": trend_regime,
                        }
                    )

            # Fade short if stretched low
            elif deviation_ratio < -stretched_threshold:
                # Confirm with price above short EMA (stalling)
                if ctx.bar.close > ema_short and ema_short > ema_long: # Short term momentum turning up
                    self.traded_today = True
                    self.cooldown_bars = 5 # Cooldown for 5 bars
                    return ctx.signal(
                        "long",
                        confidence=signal_confidence,
                        metadata={
                            "reason": "asia_session_fade_low",
                            "session_high": self.session_high,
                            "session_low": self.session_low,
                            "session_midpoint": self.session_midpoint,
                            "current_price": ctx.bar.close,
                            "deviation_ratio": deviation_ratio,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "stddev": stddev,
                            "regime": regime,
                            "trend_regime": trend_regime,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_today flag if it was an end-of-session forced close
        # Or simply log for now, as cooldown handles preventing immediate re-entry
        pass