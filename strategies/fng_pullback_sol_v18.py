from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentEventPullback(Strategy):
    METADATA = {
        "name": "SolSentimentEventPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035
        self.cooldown_bars = 16
        self.last_exit_bar = -100

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = closes[-1]
        prior_close = closes[-2]
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))
        regime = str(ctx.market.get("regime", "NORMAL"))
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        sma24 = self._sma(closes, 24)
        if sma24 is None:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close >= sma24 or sentiment > -0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_norm",
                            "close": current_close,
                            "sma24": sma24,
                            "sentiment": sentiment,
                        },
                    )
            elif pos_dir == "short":
                if current_close <= sma24 or sentiment < 0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_norm",
                            "close": current_close,
                            "sma24": sma24,
                            "sentiment": sentiment,
                        },
                    )
            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        # Long Event Setup: Fear sentiment + pullback >= 3.5% from 48-bar high + reversal tick
        pullback_ratio = (recent_high - current_close) / recent_high if recent_high > 0 else 0.0
        if sentiment < -0.30 and pullback_ratio >= self.pullback_pct:
            if current_close > prior_close:
                conf = min(0.9, 0.55 + abs(sentiment) * 0.3)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_dip_reversal_entry",
                        "sentiment": sentiment,
                        "pullback_ratio": pullback_ratio,
                        "recent_high": recent_high,
                        "close": current_close,
                    },
                )

        # Short Event Setup: Euphoria sentiment + rally >= 3.5% from 48-bar low + reversal tick
        rally_ratio = (current_close - recent_low) / recent_low if recent_low > 0 else 0.0
        if sentiment > 0.30 and rally_ratio >= self.pullback_pct:
            if current_close < prior_close:
                conf = min(0.9, 0.55 + abs(sentiment) * 0.3)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "euphoria_rally_exhaustion_entry",
                        "sentiment": sentiment,
                        "rally_ratio": rally_ratio,
                        "recent_low": recent_low,
                        "close": current_close,
                    },
                )

        return None