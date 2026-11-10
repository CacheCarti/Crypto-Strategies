from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class VolumeClimaxReversal(Strategy):
    METADATA = {
        "name": "VolumeClimaxReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.climax_volume_multiplier = 2.0  # Volume must be X times avg
        self.climax_range_min_bps = 100.0    # Min bar range in bps for a climax
        self.lookback_period = 20            # For volume and range averages
        self.cooldown_bars = 5               # Bars to wait after a trade
        self.last_signal_bar = -self.cooldown_bars

        # Volatility/Trend Regime Filters
        self.min_volatility_bps = 50.0       # Only trade if market is somewhat volatile
        self.max_crisis_score = 0.5          # Avoid trading in severe crisis
        self.avoid_strong_trend_confidence = 0.7 # Avoid trading against strong trends

        # Confidence parameters
        self.base_confidence = 0.6
        self.regime_confidence_boost = 0.1
        self.fg_confidence_boost = 0.1

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _bar_range_bps(self, high, low, close):
        if close == 0: return 0
        return ((high - low) / close) * 10000

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + self.cooldown_bars + 2)
        highs = ctx.highs(self.lookback_period + self.cooldown_bars + 2)
        lows = ctx.lows(self.lookback_period + self.cooldown_bars + 2)
        volumes = ctx.volumes(self.lookback_period + self.cooldown_bars + 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_signal_bar + self.cooldown_bars:
            return None

        # Regime Filters
        current_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Calculate average true range for volatility check
        atr = self._atr(highs, lows, closes, self.lookback_period)
        if atr is None or atr < (ctx.bar.close * self.min_volatility_bps / 10000):
            # Market not volatile enough
            return None

        if crisis_score > self.max_crisis_score:
            # Avoid crisis regimes
            return None

        # Check for volume climax on the previous bar (index -2)
        # We need at least 2 bars to check for climax and then reversal
        if len(volumes) < 3:
            return None

        prev_bar_volume = volumes[-2]
        prev_bar_high = highs[-2]
        prev_bar_low = lows[-2]
        prev_bar_close = closes[-2]
        prev_bar_open = ctx.opens(2)[0] # opens[-2]

        avg_volume = self._sma(volumes[:-2], self.lookback_period)
        if avg_volume is None or prev_bar_volume < avg_volume * self.climax_volume_multiplier:
            return None # Not a volume climax bar

        prev_bar_range_bps = self._bar_range_bps(prev_bar_high, prev_bar_low, prev_bar_close)
        if prev_bar_range_bps < self.climax_range_min_bps:
            return None # Not a significant range climax

        # Determine climax direction
        is_bull_climax = (prev_bar_close > prev_bar_open) and (prev_bar_close > closes[-3]) # Previous bar was up, after an up move
        is_bear_climax = (prev_bar_close < prev_bar_open) and (prev_bar_close < closes[-3]) # Previous bar was down, after a down move

        if not (is_bull_climax or is_bear_climax):
            return None # Not a clear directional climax

        # Check for reversal confirmation on the current bar (index -1)
        current_bar_open = ctx.bar.open
        current_bar_close = ctx.bar.close

        signal_direction = None
        reason = ""
        metadata = {
            "climax_volume": prev_bar_volume,
            "avg_volume_lookback": avg_volume,
            "climax_range_bps": prev_bar_range_bps,
            "prev_bar_close": prev_bar_close,
            "current_bar_close": current_bar_close,
            "regime": current_regime,
            "crisis_score": crisis_score,
            "trend_regime": trend_regime,
            "trend_confidence": trend_confidence,
            "fear_greed_index": fear_greed_index,
        }

        # Long signal: Bearish climax followed by a failed continuation (current bar closes higher than previous open)
        # Or current bar fails to make new low
        if is_bear_climax and current_bar_close > prev_bar_open:
            signal_direction = "long"
            reason = "bear_climax_reversal_long"
            # Adjust confidence based on regime
            confidence = self.base_confidence
            if trend_regime == "bear" and trend_confidence < self.avoid_strong_trend_confidence: # Fading a weak downtrend
                confidence += self.regime_confidence_boost
            elif trend_regime == "neutral":
                confidence += self.regime_confidence_boost / 2
            if fear_greed_index < 40: # Fear usually accompanies bottoms
                confidence += self.fg_confidence_boost

        # Short signal: Bullish climax followed by a failed continuation (current bar closes lower than previous open)
        # Or current bar fails to make new high
        elif is_bull_climax and current_bar_close < prev_bar_open:
            signal_direction = "short"
            reason = "bull_climax_reversal_short"
            # Adjust confidence based on regime
            confidence = self.base_confidence
            if trend_regime == "bull" and trend_confidence < self.avoid_strong_trend_confidence: # Fading a weak uptrend
                confidence += self.regime_confidence_boost
            elif trend_regime == "neutral":
                confidence += self.regime_confidence_boost / 2
            if fear_greed_index > 60: # Greed usually accompanies tops
                confidence += self.fg_confidence_boost

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": reason, **metadata}
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive logic based on position outcome
        # For this strategy, we maintain a fixed cooldown and confidence.
        pass

    # Helper for ATR
    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs: return None
        return sum(trs[-period:]) / period