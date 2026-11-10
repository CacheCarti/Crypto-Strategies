from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedSolPullback(Strategy):
    METADATA = {
        "name": "SentimentGatedSolPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_period = 6
        self.last_exit_bar = -999

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        sma20 = self._sma(closes, 20)
        if sma20 is None:
            return None

        current_close = ctx.bar.close

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close >= sma20 or sentiment > 0.1:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "close": current_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                        },
                    )
            elif pos_dir == "short":
                if current_close <= sma20 or sentiment < -0.1:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "close": current_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                        },
                    )
            return None

        # Cooldown guard after exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        # Long Setup: Fearful sentiment + Dip from rolling high + Intraday bounce
        if sentiment < -0.30:
            pullback_depth = (recent_high - current_close) / recent_high
            if pullback_depth >= self.pullback_pct and current_close > ctx.bar.open:
                confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_fear_pullback_bounce",
                        "sentiment": sentiment,
                        "recent_high": recent_high,
                        "pullback_depth": pullback_depth,
                        "close": current_close,
                    },
                )

        # Short Setup: Euphoric sentiment + Rally from rolling low + Intraday rejection
        if sentiment > 0.30:
            rally_height = (current_close - recent_low) / recent_low
            if rally_height >= self.pullback_pct and current_close < ctx.bar.open:
                confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_euphoria_rally_rejection",
                        "sentiment": sentiment,
                        "recent_low": recent_low,
                        "rally_height": rally_height,
                        "close": current_close,
                    },
                )

        return None