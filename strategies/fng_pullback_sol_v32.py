from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentGatedPullback(Strategy):
    METADATA = {
        "name": "SolSentimentGatedPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 21600,  # ~6 hours typical swing
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 8
        self.last_exit_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        # Regime gate to prevent negative expectancy seen in prior tests:
        # Avoid MELTDOWN / CRISIS regimes where pullbacks keep falling
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "regime": market_regime}
                )
            return None

        current_price = ctx.bar.close
        sma_val = self._sma(closes, 20)
        sentiment = float(ctx.features.get("twitter_sentiment", 0.0))

        # Check existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean-reversion exit or sentiment normalization
            if direction == "long":
                if sma_val is not None and current_price >= sma_val:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "long_mean_reversion_exit", "price": current_price, "sma20": sma_val}
                    )
                if sentiment > 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "long_sentiment_normalized", "sentiment": sentiment}
                    )
            elif direction == "short":
                if sma_val is not None and current_price <= sma_val:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "short_mean_reversion_exit", "price": current_price, "sma20": sma_val}
                    )
                if sentiment < 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "short_sentiment_normalized", "sentiment": sentiment}
                    )
            return None

        # Mandatory cooldown between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry triggers: 3% pullback from 48-bar extremum gated by sentiment extremes
        ref_high = max(highs[-self.lookback:])
        ref_low = min(lows[-self.lookback:])

        # Long: 3% pullback from recent high during fearful sentiment
        if sentiment <= -0.30:
            dip_threshold = ref_high * (1.0 - self.pullback_pct)
            if current_price <= dip_threshold:
                conf = min(1.0, 0.65 + abs(sentiment) * 0.35)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_dip_pullback",
                        "sentiment": sentiment,
                        "ref_high": ref_high,
                        "dip_threshold": dip_threshold,
                        "price": current_price
                    }
                )

        # Short: 3% rally from recent low during euphoric sentiment
        if sentiment >= 0.30:
            rally_threshold = ref_low * (1.0 + self.pullback_pct)
            if current_price >= rally_threshold:
                conf = min(1.0, 0.65 + abs(sentiment) * 0.35)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "euphoria_rally_pullback",
                        "sentiment": sentiment,
                        "ref_low": ref_low,
                        "rally_threshold": rally_threshold,
                        "price": current_price
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index