from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcSentimentContrarian(Strategy):
    METADATA = {
        "name": "BtcSentimentContrarian",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 20,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.fear_threshold = 40.0
        self.greed_threshold = 60.0
        self.exit_fear_neutral = 50.0
        self.exit_greed_neutral = 50.0
        self.cooldown_bars = 6
        self.last_exit_bar = -100

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        price = ctx.bar.close

        # Position Management & Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and (fg_index >= self.exit_fear_neutral or rsi >= 60.0):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "fg_reversion_long_exit", "fg_index": fg_index, "rsi": rsi, "price": price}
                )
            elif direction == "short" and (fg_index <= self.exit_greed_neutral or rsi <= 40.0):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "fg_reversion_short_exit", "fg_index": fg_index, "rsi": rsi, "price": price}
                )
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Loosened Contrarian Entries
        if fg_index <= self.fear_threshold and rsi < 48.0:
            return ctx.signal(
                "long",
                confidence=0.7,
                stop_loss_bps=350.0,
                take_profit_bps=700.0,
                horizon_seconds=14400,
                metadata={"reason": "fear_contrarian_long", "fg_index": fg_index, "rsi": rsi, "price": price}
            )

        if fg_index >= self.greed_threshold and rsi > 52.0:
            return ctx.signal(
                "short",
                confidence=0.7,
                stop_loss_bps=350.0,
                take_profit_bps=700.0,
                horizon_seconds=14400,
                metadata={"reason": "greed_contrarian_short", "fg_index": fg_index, "rsi": rsi, "price": price}
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index