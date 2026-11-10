from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullback(Strategy):
    METADATA = {
        "name": "SolSentimentPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.pullback_pct = 0.03
        self.lookback = 48
        self.sma_period = 24
        self.sentiment_thresh = 0.3
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        current_close = closes[-1]
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        sma24 = self._sma(closes, self.sma_period)

        if sma24 is None:
            return None

        # Check exits first
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if pos_dir == "long":
                if current_close >= sma24:
                    should_exit = True
                    exit_reason = "reversion_above_sma24"
                elif sentiment > 0.1:
                    should_exit = True
                    exit_reason = "sentiment_normalized"
            elif pos_dir == "short":
                if current_close <= sma24:
                    should_exit = True
                    exit_reason = "reversion_below_sma24"
                elif sentiment < -0.1:
                    should_exit = True
                    exit_reason = "sentiment_normalized"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "sentiment": sentiment,
                        "close": current_close,
                        "sma24": sma24,
                    },
                )
            return None

        # Entry cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        highest_high = max(highs[-self.lookback:])
        lowest_low = min(lows[-self.lookback:])

        # Long: sentiment fearful (< -0.3) + price pulled back 3%+ from 48-bar high
        pullback_from_high = (highest_high - current_close) / highest_high
        if sentiment <= -self.sentiment_thresh and pullback_from_high >= self.pullback_pct:
            # Require close to be near local trough (below SMA) to avoid entering late
            if current_close < sma24:
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_pullback_dip",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high,
                        "highest_high": highest_high,
                        "sma24": sma24,
                        "close": current_close,
                    },
                )

        # Short: sentiment euphoric (> 0.3) + price rallied 3%+ from 48-bar low
        rally_from_low = (current_close - lowest_low) / lowest_low
        if sentiment >= self.sentiment_thresh and rally_from_low >= self.pullback_pct:
            if current_close > sma24:
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "euphoria_rally_fade",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low,
                        "lowest_low": lowest_low,
                        "sma24": sma24,
                        "close": current_close,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index