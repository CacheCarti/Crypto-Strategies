from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentGatedPullback(Strategy):
    METADATA = {
        "name": "SentimentGatedPullback",
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
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        regime = ctx.market.get("regime", "NORMAL")
        sma20 = self._sma(closes, 20)

        if sma20 is None:
            return None

        # Exit check if in position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if pos_dir == "long":
                if current_close >= sma20:
                    should_exit = True
                    exit_reason = "mean_reversion_sma20_reached"
                elif sentiment > -0.05:
                    should_exit = True
                    exit_reason = "sentiment_normalized"
            elif pos_dir == "short":
                if current_close <= sma20:
                    should_exit = True
                    exit_reason = "mean_reversion_sma20_reached"
                elif sentiment < 0.05:
                    should_exit = True
                    exit_reason = "sentiment_normalized"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": exit_reason,
                        "close": current_close,
                        "sma20": sma20,
                        "sentiment": sentiment,
                        "direction": pos_dir,
                    }
                )
            return None

        # Cooldown guard after prior trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate based on prior evidence: avoid crisis/meltdown noise
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        pullback_from_high = (recent_high - current_close) / recent_high
        rally_from_low = (current_close - recent_low) / recent_low

        # Long entry: Extreme fear + meaningful dip + price turning up over previous bar
        if sentiment <= -0.30 and pullback_from_high >= self.pullback_pct:
            if current_close > closes[-2]:
                confidence = min(0.9, 0.6 + abs(sentiment) * 0.3)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_fear_pullback_bounce",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high,
                        "recent_high": recent_high,
                        "close": current_close,
                        "sma20": sma20,
                    }
                )

        # Short entry: Extreme euphoria + overextended rally + price rolling over
        if sentiment >= 0.30 and rally_from_low >= self.pullback_pct:
            if current_close < closes[-2]:
                confidence = min(0.9, 0.6 + sentiment * 0.3)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_euphoria_rally_exhaustion",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low,
                        "recent_low": recent_low,
                        "close": current_close,
                        "sma20": sma20,
                    }
                )

        return None