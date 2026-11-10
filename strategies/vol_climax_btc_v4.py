from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "VolatilityClimaxFade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for a few hours hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"], # For market trend context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.climax_volume_multiplier = 2.5
        self.climax_range_multiplier = 2.0
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period=14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1)
        highs = ctx.highs(self.vol_period + 1)
        lows = ctx.lows(self.vol_period + 1)
        volumes = ctx.volumes(self.vol_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Regime filter: Only trade in 'normal' or 'volatile' regimes
        # and not in strong crisis
        if ctx.market.get("regime", "NORMAL") not in ["NORMAL", "VOLATILE"]:
             return None
        if ctx.market.get("crisis_score", 0.0) > 0.5:
             return None

        # Trend filter: Only trade if market trend is neutral or we are fading against a weak trend
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume

        # Calculate average volume and range for the lookback period
        avg_volume = self._sma(volumes[:-1], self.vol_period)
        if avg_volume is None or avg_volume == 0:
            return None

        # Calculate average true range
        avg_atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.vol_period)
        if avg_atr is None or avg_atr == 0:
            return None
        
        current_range = current_high - current_low

        # Volatility Climax Detection: High volume AND High range
        is_vol_climax = (current_volume > avg_volume * self.climax_volume_multiplier and
                         current_range > avg_atr * self.climax_range_multiplier)

        if not is_vol_climax:
            return None

        # Check the *previous* bar for climax and current bar for failure to follow through
        # We need the bar BEFORE the current one to be the climax bar.
        # So we look at closes[-2], highs[-2], lows[-2], volumes[-2] for climax detection
        # and closes[-1] for failure to continue.
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]

        prev_range = prev_high - prev_low

        # Recalculate avg_volume and avg_atr excluding the last two bars
        # This is to make sure our average is truly from *before* the climax
        avg_volume_for_prev = self._sma(volumes[:-2], self.vol_period)
        avg_atr_for_prev = self._atr(highs[:-2], lows[:-2], closes[:-2], self.vol_period)

        if avg_volume_for_prev is None or avg_volume_for_prev == 0 or \
           avg_atr_for_prev is None or avg_atr_for_prev == 0:
            return None

        is_prev_bar_climax = (prev_volume > avg_volume_for_prev * self.climax_volume_multiplier and
                              prev_range > avg_atr_for_prev * self.climax_range_multiplier)

        if not is_prev_bar_climax:
            return None

        # Now, check if the current bar (closes[-1]) fails to continue the previous climax direction
        # This means the climax was either an exhaustion high (bearish fade) or an exhaustion low (bullish fade).

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Bearish Fade: Previous bar was a strong up-move climax, current bar reverses/stalls
        if prev_close < closes[-2] and current_close < prev_close and \
           (trend_regime == "neutral" or (trend_regime == "bear" and trend_confidence < 0.6)):
            # Previous bar was a strong up-climax, current bar failed to continue higher
            signal_direction = "short"
            reason = "bearish_volatility_climax_fade"
            confidence = 0.75 # High confidence for a fade
            self.last_trade_bar = ctx.bar_index

        # Bullish Fade: Previous bar was a strong down-move climax, current bar reverses/stalls
        elif prev_close > closes[-2] and current_close > prev_close and \
             (trend_regime == "neutral" or (trend_regime == "bull" and trend_confidence < 0.6)):
            # Previous bar was a strong down-climax, current bar failed to continue lower
            signal_direction = "long"
            reason = "bullish_volatility_climax_fade"
            confidence = 0.75 # High confidence for a fade
            self.last_trade_bar = ctx.bar_index

        if signal_direction and not ctx.has_position():
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "climax_volume_ratio": current_volume / avg_volume_for_prev,
                    "climax_range_ratio": current_range / avg_atr_for_prev,
                    "current_price": current_close,
                    "prev_close": prev_close,
                    "regime": ctx.market.get("regime"),
                    "trend_regime": trend_regime,
                }
            )
        elif ctx.has_position() and ctx.position_direction() != signal_direction and signal_direction:
            # If we have a position and a new signal in the opposite direction appears, flip it.
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": f"flip_on_{reason}",
                    "climax_volume_ratio": current_volume / avg_volume_for_prev,
                    "climax_range_ratio": current_range / avg_atr_for_prev,
                    "current_price": current_close,
                    "prev_close": prev_close,
                    "regime": ctx.market.get("regime"),
                    "trend_regime": trend_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, if needed
        pass