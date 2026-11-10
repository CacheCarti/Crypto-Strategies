from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 43200,  # ~12 hours swing hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_rsi = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 36

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi_val = self._rsi(closes, self.period_rsi)
        if rsi_val is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        price = ctx.bar.close

        # Manage open position
        if ctx.has_position():
            pos = ctx.position or {}
            entry_price = pos.get("entry_price", price)
            ret_bps = ((price - entry_price) / entry_price) * 10000.0 if entry_price > 0 else 0.0

            # Exit when conditions normalize or profit target / time horizon is reached
            exit_reason = None
            if rsi_val >= 58.0:
                exit_reason = "rsi_normalized_profit_take"
            elif funding > 0.00015:
                exit_reason = "funding_flipped_positive"
            elif fg_index > 50.0:
                exit_reason = "sentiment_normalized"

            if exit_reason:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi_val, 2),
                        "funding": funding,
                        "fear_greed": fg_index,
                        "return_bps": round(ret_bps, 1),
                        "price": price,
                    },
                )
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: Confluence of negative/neutral-depressed funding, fear sentiment, and oversold price action
        funding_condition = funding <= 0.00002
        fear_condition = fg_index <= 38.0
        rsi_condition = rsi_val <= 38.0

        if funding_condition and fear_condition and rsi_condition:
            # Score conviction smoothly based on how extreme the legs are
            conf_funding = 0.5 + min(max(-funding * 5000.0, 0.0), 0.3)
            conf_fg = 0.5 + ((38.0 - fg_index) / 38.0) * 0.3
            conf_rsi = 0.5 + ((38.0 - rsi_val) / 38.0) * 0.2
            confidence = min(max((conf_funding + conf_fg + conf_rsi) / 3.0, 0.55), 0.95)

            return ctx.signal(
                "long",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(rsi_val, 2),
                    "funding_rate": funding,
                    "fear_greed": fg_index,
                    "price": price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index