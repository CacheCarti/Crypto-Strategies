from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDynamicPullback(Strategy):
    METADATA = {
        "name": "SolSentimentDynamicPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # ~8h target hold for swing pullback
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.pullback_pct = 0.035  # 3.5% dynamic drawdown/rally threshold
        self.mean_sma_period = 24

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Mandatory cooldown to avoid friction bleed and limit trades to ~20-80
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))
        mean_price = self._sma(closes, self.mean_sma_period)
        if mean_price is None:
            return None

        # --- Position Management (Dynamic Exit on normalization or mean reversion) ---
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                # Exit when price recovers to/above mean or sentiment turns euphoric
                if current_close >= mean_price or sentiment >= 0.20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_take_profit_mean_or_sentiment_normalized",
                            "price": current_close,
                            "mean_price": mean_price,
                            "sentiment": sentiment,
                        },
                    )
            elif direction == "short":
                # Exit when price recovers below mean or sentiment turns fearful
                if current_close <= mean_price or sentiment <= -0.20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_take_profit_mean_or_sentiment_normalized",
                            "price": current_close,
                            "mean_price": mean_price,
                            "sentiment": sentiment,
                        },
                    )
            return None

        # --- Entry Gating ---
        # Lookback peak and trough excluding current bar to prevent lookahead
        rolling_high = max(highs[-self.lookback:-1])
        rolling_low = min(lows[-self.lookback:-1])

        pullback_from_high = (rolling_high - current_close) / rolling_high
        rally_from_low = (current_close - rolling_low) / rolling_low

        # Micro-bounce validation: prevent catching falling knives
        prev_close = closes[-2]
        bouncing_up = current_close > prev_close
        bouncing_down = current_close < prev_close

        # Long Entry: Fear state + sufficient pullback + local bounce
        if sentiment <= -0.25 and pullback_from_high >= self.pullback_pct and bouncing_up:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_dip_pullback_long",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "rolling_high": rolling_high,
                    "price": current_close,
                },
            )

        # Short Entry: Euphoria state + sufficient rally + local topping tick
        if sentiment >= 0.25 and rally_from_low >= self.pullback_pct and bouncing_down:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_rally_fade_short",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "rolling_low": rolling_low,
                    "price": current_close,
                },
            )

        return None