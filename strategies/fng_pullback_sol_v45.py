from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDipReversion(Strategy):
    METADATA = {
        "name": "SolSentimentDipReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035       # 3.5% deep pullback/rally for high conviction
        self.cooldown_bars = 6          # 6 bars post-close cooldown
        self.last_exit_bar = -100
        self.min_bars_between = 4
        self.last_entry_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        # Cooldown guard after exit or recent trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.min_bars_between:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # Skip entries in extreme crisis or meltdown to protect alpha
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        current_close = closes[-1]
        sma20 = self._sma(closes, 20)
        rsi = self._rsi(closes, 14)

        if sma20 is None or rsi is None:
            return None

        # Position management / Exit check
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean-reversion exit or sentiment normalization
            if direction == "long":
                if current_close >= sma20 or sentiment > 0.15 or rsi > 60.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_target_hit",
                            "sentiment": sentiment,
                            "rsi": rsi,
                            "close": current_close,
                            "sma20": sma20
                        }
                    )
            elif direction == "short":
                if current_close <= sma20 or sentiment < -0.15 or rsi < 40.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_target_hit",
                            "sentiment": sentiment,
                            "rsi": rsi,
                            "close": current_close,
                            "sma20": sma20
                        }
                    )
            return None

        # Entry logic: selective high-conviction pullbacks
        highest_48 = max(highs[-self.lookback:])
        lowest_48 = min(lows[-self.lookback:])

        pullback_from_high = (highest_48 - current_close) / highest_48
        rally_from_low = (current_close - lowest_48) / lowest_48

        # Buy fear dip: sentiment fearful, deep pullback from 48h high, RSI confirms local oversold
        if sentiment <= -0.30 and pullback_from_high >= self.pullback_pct and rsi < 38.0:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.9, 0.6 + abs(sentiment) * 0.3)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "fear_sentiment_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "rsi": rsi,
                    "highest_48": highest_48
                }
            )

        # Sell euphoria rally: sentiment euphoric, extended rally from 48h low, RSI confirms local overbought
        if sentiment >= 0.30 and rally_from_low >= self.pullback_pct and rsi > 62.0:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.9, 0.6 + sentiment * 0.3)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "euphoria_sentiment_rally_exhaustion",
                    "sentiment": sentiment,
                    "rally_pct": rally_from_low,
                    "rsi": rsi,
                    "lowest_48": lowest_48
                }
            )

        return None