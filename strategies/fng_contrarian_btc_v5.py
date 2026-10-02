from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class BtcFearGreedContrarian(Strategy):
    METADATA = {
        "name": "BTC Fear & Greed Contrarian Reversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 20,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 32.0
        self.greed_threshold = 68.0
        self.neutral_low = 42.0
        self.neutral_high = 58.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle active position exit: return to neutral sentiment
        if has_pos:
            is_neutral = self.neutral_low <= fg_index <= self.neutral_high
            if pos_dir == "long" and (is_neutral or fg_index >= self.greed_threshold):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_neutral_sentiment_exit",
                        "fear_greed": fg_index,
                        "close": ctx.bar.close,
                    },
                )
            elif pos_dir == "short" and (is_neutral or fg_index <= self.fear_threshold):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_neutral_sentiment_exit",
                        "fear_greed": fg_index,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Contrarian Long: Crowd in fear zone
        if fg_index <= self.fear_threshold:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=700.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "fear_sentiment_contrarian_long",
                    "fear_greed": fg_index,
                    "close": ctx.bar.close,
                },
            )

        # Contrarian Short: Crowd in greed zone
        if fg_index >= self.greed_threshold:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=700.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "greed_sentiment_contrarian_short",
                    "fear_greed": fg_index,
                    "close": ctx.bar.close,
                },
            )

        return None