from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentMeanReversion(Strategy):
    METADATA = {
        "name": "SolSentimentMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

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

        current_close = ctx.bar.close
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # Baseline mean for reversion target
        sma_20 = self._sma(closes, 20)
        if sma_20 is None:
            return None

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Target exit: reversion to 20-bar SMA or sentiment normalization
            if pos_dir == "long":
                if current_close >= sma_20 or sentiment >= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_reverted_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "close": current_close,
                            "sma_20": sma_20,
                            "bars_held": bars_held
                        }
                    )
            elif pos_dir == "short":
                if current_close <= sma_20 or sentiment <= 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_reverted_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "close": current_close,
                            "sma_20": sma_20,
                            "bars_held": bars_held
                        }
                    )
            return None

        # Cooldown guard to avoid rapid overtrading
        if bars_since_exit < self.cooldown_bars:
            return None

        # Extremes over the lookback window (excluding current bar to detect pullback from rolling anchor)
        recent_high = max(highs[-self.lookback:-1])
        recent_low = min(lows[-self.lookback:-1])

        # Entry logic: Fearful dip buying
        if sentiment <= -0.25:
            pullback_from_high = (recent_high - current_close) / recent_high
            if pullback_from_high >= self.pullback_pct and current_close < sma_20:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=320.0,
                    take_profit_bps=480.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "sentiment_fear_pullback_dip",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high,
                        "recent_high": recent_high,
                        "close": current_close,
                        "sma_20": sma_20
                    }
                )

        # Entry logic: Euphoric rally fading
        if sentiment >= 0.25:
            rally_from_low = (current_close - recent_low) / recent_low
            if rally_from_low >= self.pullback_pct and current_close > sma_20:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=320.0,
                    take_profit_bps=480.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "sentiment_euphoria_rally_fade",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low,
                        "recent_low": recent_low,
                        "close": current_close,
                        "sma_20": sma_20
                    }
                )

        return None