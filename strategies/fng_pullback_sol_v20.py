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
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 12
        self.last_trade_bar = -999

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_price = self._sma(closes, 24)
        if mean_price is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if sentiment > -0.05 or current_close >= mean_price:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "close": current_close,
                            "mean_price": mean_price,
                        },
                    )
            elif pos_dir == "short":
                if sentiment < 0.05 or current_close <= mean_price:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "sentiment": sentiment,
                            "close": current_close,
                            "mean_price": mean_price,
                        },
                    )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Calculate historical extremes over the 48-bar reference window (excluding current bar)
        window_high = max(highs[-self.lookback - 1 : -1])
        window_low = min(lows[-self.lookback - 1 : -1])

        # Strict event boundaries: state transition across the 3% boundary
        long_threshold = window_high * (1.0 - self.pullback_pct)
        short_threshold = window_low * (1.0 + self.pullback_pct)

        long_event = prev_close >= long_threshold and current_close < long_threshold
        short_event = prev_close <= short_threshold and current_close > short_threshold

        # Long entry: Fear filter + fresh downward cross beyond 3% pullback
        if sentiment < -0.30 and long_event:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "fear_dip_event_cross",
                    "sentiment": sentiment,
                    "close": current_close,
                    "window_high": window_high,
                    "threshold": long_threshold,
                },
            )

        # Short entry: Euphoria filter + fresh upward cross beyond 3% rally
        if sentiment > 0.30 and short_event:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "euphoria_rally_event_cross",
                    "sentiment": sentiment,
                    "close": current_close,
                    "window_low": window_low,
                    "threshold": short_threshold,
                },
            )

        return None