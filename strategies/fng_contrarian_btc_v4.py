from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class BtcSentimentContrarian(Strategy):
    METADATA = {
        "name": "BtcSentimentContrarian",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.fear_threshold = 40.0
        self.greed_threshold = 60.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        fg = float(ctx.features.get("fear_greed_index", 50.0))

        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and (fg >= 50.0 or rsi >= 55.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "sentiment_neutral_or_rsi_target_exit",
                        "fear_greed": fg,
                        "rsi": rsi,
                        "close": ctx.bar.close,
                    },
                )
            if direction == "short" and (fg <= 50.0 or rsi <= 45.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "sentiment_neutral_or_rsi_target_exit",
                        "fear_greed": fg,
                        "rsi": rsi,
                        "close": ctx.bar.close,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if fg <= self.fear_threshold and rsi < 45.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=300.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "fear_sentiment_oversold_long",
                    "fear_greed": fg,
                    "rsi": rsi,
                    "close": ctx.bar.close,
                },
            )

        if fg >= self.greed_threshold and rsi > 55.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=300.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "greed_sentiment_overbought_short",
                    "fear_greed": fg,
                    "rsi": rsi,
                    "close": ctx.bar.close,
                },
            )

        return None