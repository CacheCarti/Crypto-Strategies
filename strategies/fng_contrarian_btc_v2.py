from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class FearGreedContrarianSwing(Strategy):
    METADATA = {
        "name": "FearGreedContrarianSwing",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 10,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 35.0
        self.greed_threshold = 65.0
        self.neutral_low = 45.0
        self.neutral_high = 55.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        fg = float(ctx.features.get("fear_greed_index", 50.0))
        price = ctx.bar.close

        # Position Management: Exit when sentiment normalizes into neutral zone
        if ctx.has_position():
            if self.neutral_low <= fg <= self.neutral_high:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "sentiment_reverted_to_neutral",
                        "fear_greed_index": fg,
                        "price": price,
                    },
                )
            return None

        # Cooldown check between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry Long: Market in fear zone (contrarian buy)
        if fg <= self.fear_threshold:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=650.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "contrarian_fear_dip_entry",
                    "fear_greed_index": fg,
                    "price": price,
                },
            )

        # Entry Short: Market in greed zone (contrarian sell)
        if fg >= self.greed_threshold:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=650.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "contrarian_greed_top_entry",
                    "fear_greed_index": fg,
                    "price": price,
                },
            )

        return None