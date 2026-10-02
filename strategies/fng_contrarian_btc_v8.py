from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class FearGreedContrarianSwing(Strategy):
    METADATA = {
        "name": "FearGreedContrarianSwing",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 10,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 30.0
        self.greed_threshold = 70.0
        self.neutral_low = 42.0
        self.neutral_high = 58.0
        self.cooldown_bars = 8
        self.last_exit_bar = -100

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        fg = float(ctx.features.get("fear_greed_index", 50.0))
        price = ctx.bar.close

        if ctx.has_position():
            direction = ctx.position_direction()
            # Exit long once sentiment recovers to neutral or higher
            if direction == "long" and fg >= self.neutral_low:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "fear_sentiment_reversion_to_neutral",
                        "fear_greed": fg,
                        "price": price,
                    },
                )
            # Exit short once sentiment cools down to neutral or lower
            if direction == "short" and fg <= self.neutral_high:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "greed_sentiment_reversion_to_neutral",
                        "fear_greed": fg,
                        "price": price,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long on capitulation / fear
        if fg <= self.fear_threshold:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "contrarian_long_extreme_fear",
                    "fear_greed": fg,
                    "price": price,
                },
            )

        # Short on euphoria / extreme greed
        if fg >= self.greed_threshold:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "contrarian_short_extreme_greed",
                    "fear_greed": fg,
                    "price": price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index