from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentMeanReversion(Strategy):
    METADATA = {
        "name": "SolSentimentMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.pullback_pct = 0.03
        self.lookback = 48
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 18

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # Dynamic mean proxy: 24-period SMA
        mean_24 = sum(closes[-24:]) / 24.0

        # Dynamic volatility stop calculation via ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            atr = current_close * 0.015

        # Handle active positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", current_close) if ctx.position else current_close
            bars_held = ctx.position.get("bars_held", 0) if ctx.position else 0

            # 1. Timed exit to prevent capital lockup
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "timed_max_hold_exit",
                    "bars_held": bars_held,
                    "sentiment": sentiment,
                    "close": current_close
                })

            # 2. Structural Volatility Stop (2.2 * ATR)
            vol_stop_dist = 2.2 * atr
            if pos_dir == "long" and current_close < (entry_price - vol_stop_dist):
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "volatility_stop_long",
                    "entry_price": entry_price,
                    "close": current_close,
                    "atr": atr
                })
            elif pos_dir == "short" and current_close > (entry_price + vol_stop_dist):
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "volatility_stop_short",
                    "entry_price": entry_price,
                    "close": current_close,
                    "atr": atr
                })

            # 3. Take Profit / Mean Reversion & Sentiment Normalization exit
            if pos_dir == "long":
                if current_close >= mean_24 or sentiment > -0.05:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "mean_or_sentiment_normalized_long",
                        "mean_24": mean_24,
                        "sentiment": sentiment,
                        "close": current_close
                    })
            elif pos_dir == "short":
                if current_close <= mean_24 or sentiment < 0.05:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "mean_or_sentiment_normalized_short",
                        "mean_24": mean_24,
                        "sentiment": sentiment,
                        "close": current_close
                    })
            return None

        # Mandatory cooldown post-exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate high/low reference over lookback period (excluding current bar)
        highest_prev = max(highs[-self.lookback - 1:-1])
        lowest_prev = min(lows[-self.lookback - 1:-1])

        pullback_from_high = (highest_prev - current_close) / highest_prev
        rally_from_low = (current_close - lowest_prev) / lowest_prev

        # Long Entry: Fearful market + 3% dip from lookback high + close slightly above 3-bar low (local stabilization)
        min_3b = min(lows[-3:])
        if sentiment <= -0.30 and pullback_from_high >= self.pullback_pct and current_close >= min_3b:
            confidence = min(0.9, 0.5 + abs(sentiment) * 0.4)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_fear_pullback_dip",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "highest_prev": highest_prev,
                    "atr": atr,
                    "mean_24": mean_24
                }
            )

        # Short Entry: Euphoric market + 3% rally from lookback low + close slightly below 3-bar high
        max_3b = max(highs[-3:])
        if sentiment >= 0.30 and rally_from_low >= self.pullback_pct and current_close <= max_3b:
            confidence = min(0.9, 0.5 + sentiment * 0.4)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_euphoria_rally_stretch",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "lowest_prev": lowest_prev,
                    "atr": atr,
                    "mean_24": mean_24
                }
            )

        return None