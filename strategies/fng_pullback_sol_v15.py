from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDipReversion(Strategy):
    METADATA = {
        "name": "SolSentimentDipReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold for 1h swing
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035       # 3.5% dip/rally requirement for high conviction
        self.cooldown_bars = 8          # Mandatory cooldown between trades
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        # Slow sentiment filter: proxy derived from fear & greed in backtest
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        mean_price = self._sma(closes, 24)
        current_price = ctx.bar.close

        # Manage existing positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Mean reversion or sentiment normalization exit
            if pos_dir == "long":
                if (mean_price is not None and current_price >= mean_price) or sentiment > 0.05 or bars_held >= 24:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "price": current_price,
                            "mean": mean_price or 0.0,
                            "sentiment": sentiment,
                            "bars_held": bars_held,
                        },
                    )
            elif pos_dir == "short":
                if (mean_price is not None and current_price <= mean_price) or sentiment < -0.05 or bars_held >= 24:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "price": current_price,
                            "mean": mean_price or 0.0,
                            "sentiment": sentiment,
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Cooldown guard: prevent overtrading and friction bleeding
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety: avoid buying dips during full meltdown crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        highest_high = max(highs[-self.lookback:])
        lowest_low = min(lows[-self.lookback:])
        rsi = self._rsi(closes, 14)

        if rsi is None or highest_high <= 0 or lowest_low <= 0:
            return None

        pullback_from_high = (highest_high - current_price) / highest_high
        rally_from_low = (current_price - lowest_low) / lowest_low

        # Long Setup: Extreme fear + meaningful dip + local oversold bounce context
        if sentiment < -0.25 and pullback_from_high >= self.pullback_pct and rsi < 38.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.55 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_dip_oversold_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "rsi": rsi,
                    "price": current_price,
                    "high_48": highest_high,
                },
            )

        # Short Setup: Extreme euphoria + strong run-up + overbought exhaustion
        if sentiment > 0.25 and rally_from_low >= self.pullback_pct and rsi > 64.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.55 + abs(sentiment) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "euphoria_rally_overbought_exhaustion",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "rsi": rsi,
                    "price": current_price,
                    "low_48": lowest_low,
                },
            )

        return None