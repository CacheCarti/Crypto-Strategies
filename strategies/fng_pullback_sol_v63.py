from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullbackSol(Strategy):
    METADATA = {
        "name": "SentimentGatedPullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
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
        curr_price = ctx.bar.close
        sma20 = self._sma(closes, 20)

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            reason = ""

            if pos_dir == "long":
                if sentiment >= 0.0:
                    should_exit = True
                    reason = "sentiment_normalized_long"
                elif sma20 is not None and curr_price >= sma20:
                    should_exit = True
                    reason = "mean_reversion_hit_long"
            elif pos_dir == "short":
                if sentiment <= 0.0:
                    should_exit = True
                    reason = "sentiment_normalized_short"
                elif sma20 is not None and curr_price <= sma20:
                    should_exit = True
                    reason = "mean_reversion_hit_short"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "sentiment": sentiment,
                        "price": curr_price,
                        "sma20": sma20 or curr_price,
                    }
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        recent_highs = highs[-self.lookback:-1]
        recent_lows = lows[-self.lookback:-1]
        if not recent_highs or not recent_lows:
            return None

        max_high = max(recent_highs)
        min_low = min(recent_lows)

        long_pullback = (max_high - curr_price) / max_high
        if sentiment <= -0.3 and long_pullback >= self.pullback_pct:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": long_pullback,
                    "rolling_high": max_high,
                    "price": curr_price,
                }
            )

        short_rally = (curr_price - min_low) / min_low
        if sentiment >= 0.3 and short_rally >= self.pullback_pct:
            confidence = min(0.9, 0.5 + sentiment * 0.4)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "euphoria_sentiment_rally_reversal",
                    "sentiment": sentiment,
                    "rally_pct": short_rally,
                    "rolling_low": min_low,
                    "price": curr_price,
                }
            )

        return None