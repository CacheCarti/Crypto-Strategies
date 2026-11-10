from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage open position duration exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_hold_timeout", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown filter
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown regimes to prevent severe whipsaws
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # 24-bar price return
        price_now = closes[-1]
        price_prev = closes[-self.lookback]
        ret_24 = (price_now - price_prev) / price_prev

        # Short-term momentum confirmation (6-bar)
        ret_6 = (price_now - closes[-6]) / closes[-6]

        # Squeeze conditions:
        # Long Squeeze: Price is up over 24h & continuing short-term, but funding remains negative (shorts trapped).
        long_squeeze = (ret_24 > 0.012) and (ret_6 > 0.002) and (funding < -0.00003)

        # Short Squeeze: Price is down over 24h & declining short-term, but funding remains positive (longs trapped).
        short_squeeze = (ret_24 < -0.012) and (ret_6 < -0.002) and (funding > 0.00005)

        if long_squeeze:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.55 + abs(funding) * 1500)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding": funding,
                    "ret_24": ret_24,
                    "ret_6": ret_6,
                    "price": price_now,
                }
            )

        if short_squeeze:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.55 + abs(funding) * 1500)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_squeeze_flush",
                    "funding": funding,
                    "ret_24": ret_24,
                    "ret_6": ret_6,
                    "price": price_now,
                }
            )

        return None