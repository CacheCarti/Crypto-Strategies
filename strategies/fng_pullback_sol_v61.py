from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullbackSol(Strategy):
    METADATA = {
        "name": "SentimentGatedPullbackSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 24

    def _sma(self, values, period: int):
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

        current_price = ctx.bar.close
        sma20 = self._sma(closes, 20)
        if sma20 is None:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Time-based stop to ensure trade independence and cycle capital
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held, "sentiment": sentiment}
                )

            # Reversion exit: price crosses back through SMA20 or sentiment normalizes
            if pos_dir == "long":
                if current_price >= sma20 or sentiment > -0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_norm",
                            "price": current_price,
                            "sma20": sma20,
                            "sentiment": sentiment
                        }
                    )
            elif pos_dir == "short":
                if current_price <= sma20 or sentiment < 0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_norm",
                            "price": current_price,
                            "sma20": sma20,
                            "sentiment": sentiment
                        }
                    )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # High/low reference excluding the current bar
        ref_high = max(highs[-self.lookback - 1:-1])
        ref_low = min(lows[-self.lookback - 1:-1])

        pullback_from_high = (ref_high - current_price) / ref_high
        rally_from_low = (current_price - ref_low) / ref_low

        # Filter crisis regime
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup: Fearful sentiment + Dip >= 3% from recent high
        if sentiment <= -0.30 and pullback_from_high >= self.pullback_pct:
            # Confirm price is oversold relative to the mean to avoid catching falling knives
            if current_price < sma20:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_sentiment_dip_buy",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high,
                        "ref_high": ref_high,
                        "sma20": sma20,
                        "price": current_price
                    }
                )

        # Short Setup: Euphoric sentiment + Rally >= 3% from recent low
        if sentiment >= 0.30 and rally_from_low >= self.pullback_pct:
            # Confirm price is stretched above the mean
            if current_price > sma20:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "euphoria_sentiment_rally_sell",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low,
                        "ref_low": ref_low,
                        "sma20": sma20,
                        "price": current_price
                    }
                )

        return None