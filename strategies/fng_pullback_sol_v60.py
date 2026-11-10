from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullbackEvent(Strategy):
    METADATA = {
        "name": "SolSentimentPullbackEvent",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        current_close = closes[-1]
        prev_close = closes[-2]
        current_bar = ctx.bar_index

        sma20 = self._sma(closes, 20)
        if sma20 is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Long exit: mean reversion reached or sentiment normalized
            if pos_dir == "long":
                if current_close >= sma20 or sentiment > -0.1:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "close": current_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                        }
                    )

            # Short exit: mean reversion reached or sentiment normalized
            elif pos_dir == "short":
                if current_close <= sma20 or sentiment < 0.1:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "close": current_close,
                            "sma20": sma20,
                            "sentiment": sentiment,
                        }
                    )
            return None

        # Entry gating & cooldown
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        highest_high = max(highs[-self.lookback-1:-1])
        lowest_low = min(lows[-self.lookback-1:-1])

        long_threshold = highest_high * (1.0 - self.pullback_pct)
        short_threshold = lowest_low * (1.0 + self.pullback_pct)

        # Discrete boundary crossing: only trigger at the precise event of crossing the threshold
        crossed_pullback_down = (prev_close >= long_threshold) and (current_close < long_threshold)
        crossed_rally_up = (prev_close <= short_threshold) and (current_close > short_threshold)

        # Long: Fearful sentiment + discrete pullback breach
        if sentiment < -0.3 and crossed_pullback_down:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "fear_pullback_boundary_cross",
                    "sentiment": sentiment,
                    "close": current_close,
                    "highest_high": highest_high,
                    "threshold": long_threshold,
                }
            )

        # Short: Euphoric sentiment + discrete rally breach
        if sentiment > 0.3 and crossed_rally_up:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "euphoria_rally_boundary_cross",
                    "sentiment": sentiment,
                    "close": current_close,
                    "lowest_low": lowest_low,
                    "threshold": short_threshold,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index