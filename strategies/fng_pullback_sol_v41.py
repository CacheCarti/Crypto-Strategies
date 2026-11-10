from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullbackSol(Strategy):
    METADATA = {
        "name": "SentimentGatedPullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_trade = 0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        current_close = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_24 = self._sma(closes, 24)
        rsi_val = self._rsi(closes, 14)

        if mean_24 is None or rsi_val is None:
            return None

        # Manage existing position exits
        if ctx.has_position():
            self.bars_in_trade += 1
            direction = ctx.position_direction()

            # Exit on mean reversion, sentiment normalization, or time limit
            if direction == "long":
                if current_close >= mean_24 or sentiment > -0.05 or self.bars_in_trade >= 20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_reset",
                            "close": current_close,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_trade,
                        },
                    )
            elif direction == "short":
                if current_close <= mean_24 or sentiment < 0.05 or self.bars_in_trade >= 20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_reset",
                            "close": current_close,
                            "sma24": mean_24,
                            "sentiment": sentiment,
                            "bars_held": self.bars_in_trade,
                        },
                    )
            return None

        # Mandatory cooldown between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute rolling high/low over lookback
        highest_48 = max(highs[-self.lookback:])
        lowest_48 = min(lows[-self.lookback:])

        pullback_from_high = (highest_48 - current_close) / highest_48
        rally_from_low = (current_close - lowest_48) / lowest_48

        # Long: Fearful sentiment + at least 3% dip from 48-bar high + RSI not in freefall collapse
        if sentiment < -0.3 and pullback_from_high >= self.pullback_pct and rsi_val <= 42:
            self.bars_in_trade = 0
            conf = min(0.85, 0.5 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": round(pullback_from_high * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "high48": highest_48,
                    "close": current_close,
                },
            )

        # Short: Euphoric sentiment + at least 3% rally from 48-bar low + overextended RSI
        if sentiment > 0.3 and rally_from_low >= self.pullback_pct and rsi_val >= 58:
            self.bars_in_trade = 0
            conf = min(0.85, 0.5 + abs(sentiment) * 0.3)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_rally_fade",
                    "sentiment": sentiment,
                    "rally_pct": round(rally_from_low * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "low48": lowest_48,
                    "close": current_close,
                },
            )

        return None