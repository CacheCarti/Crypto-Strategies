from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class BtcSentimentContrarian(Strategy):
    METADATA = {
        "name": "BtcSentimentContrarian",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 20,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 35.0
        self.greed_threshold = 65.0
        self.neutral_low = 45.0
        self.neutral_high = 55.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
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
        closes = ctx.closes(20)
        if len(closes) < 15:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        current_price = ctx.bar.close

        # Position exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if fg_index >= self.neutral_low or rsi >= 60.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "sentiment_rebound_or_rsi_neutralized",
                            "fear_greed": fg_index,
                            "rsi": round(rsi, 2),
                            "price": current_price,
                        },
                    )
            elif pos_dir == "short":
                if fg_index <= self.neutral_high or rsi <= 40.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "sentiment_pullback_or_rsi_neutralized",
                            "fear_greed": fg_index,
                            "rsi": round(rsi, 2),
                            "price": current_price,
                        },
                    )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Loosened entry triggers: broad sentiment fear/greed boundaries
        if fg_index <= self.fear_threshold and rsi < 50.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "fear_sentiment_contrarian_long",
                    "fear_greed": fg_index,
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        if fg_index >= self.greed_threshold and rsi > 50.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "greed_sentiment_contrarian_short",
                    "fear_greed": fg_index,
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        return None