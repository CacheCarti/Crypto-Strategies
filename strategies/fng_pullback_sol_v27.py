from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentMeanReversion(Strategy):
    METADATA = {
        "name": "SolSentimentMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14):
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

        if len(closes) < self.lookback:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        curr_price = ctx.bar.close
        sma_val = self._sma(closes, self.lookback)
        if sma_val is None:
            return None

        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val is None or atr_val == 0:
            return None

        vol_sl_bps = max(250.0, min(500.0, (2.2 * atr_val / curr_price) * 10000.0))
        vol_tp_bps = max(350.0, min(650.0, (2.8 * atr_val / curr_price) * 10000.0))

        if ctx.has_position():
            direction = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if direction == "long":
                if curr_price >= sma_val:
                    should_exit = True
                    exit_reason = "reverted_to_mean_long"
                elif sentiment > -0.05:
                    should_exit = True
                    exit_reason = "sentiment_normalized_long"
            elif direction == "short":
                if curr_price <= sma_val:
                    should_exit = True
                    exit_reason = "reverted_to_mean_short"
                elif sentiment < 0.05:
                    should_exit = True
                    exit_reason = "sentiment_normalized_short"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "price": curr_price,
                        "sma": sma_val,
                        "sentiment": sentiment,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        highest_high = max(highs[-self.lookback:])
        lowest_low = min(lows[-self.lookback:])

        pullback_from_high = (highest_high - curr_price) / highest_high
        rally_from_low = (curr_price - lowest_low) / lowest_low

        if sentiment <= -0.30 and pullback_from_high >= self.pullback_pct and curr_price < sma_val:
            conf = min(1.0, max(0.5, 0.5 + abs(sentiment) * 0.4))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=vol_sl_bps,
                take_profit_bps=vol_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "highest_high": highest_high,
                    "sma": sma_val,
                    "atr_sl_bps": vol_sl_bps,
                },
            )

        if sentiment >= 0.30 and rally_from_low >= self.pullback_pct and curr_price > sma_val:
            conf = min(1.0, max(0.5, 0.5 + abs(sentiment) * 0.4))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=vol_sl_bps,
                take_profit_bps=vol_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "euphoria_sentiment_rally_exhaustion",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "lowest_low": lowest_low,
                    "sma": sma_val,
                    "atr_sl_bps": vol_sl_bps,
                },
            )

        return None