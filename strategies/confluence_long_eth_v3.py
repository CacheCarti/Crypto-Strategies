from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class TripleConfluenceReversion(Strategy):
    METADATA = {
        "name": "Triple Confluence Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 28800,  # 8 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.rsi_entry_thresh = 36.0
        self.rsi_exit_thresh = 56.0
        self.fg_thresh = 40.0
        self.funding_max_thresh = 0.00003  # Negative or neutral-discounted funding
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _rsi(self, closes, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        current_price = ctx.bar.close

        # Position Management & Exit
        if ctx.has_position():
            # Exit if RSI reaches normalization or crowd turns aggressively long
            should_exit = rsi >= self.rsi_exit_thresh or funding > 0.0002
            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "confluence_normalized_exit",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "fg_index": fg_index,
                        "price": current_price,
                    },
                )
            return None

        # Entry Cooldown Check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Triple Confluence Trigger
        rsi_condition = rsi < self.rsi_entry_thresh
        fg_condition = fg_index <= self.fg_thresh
        funding_condition = funding <= self.funding_max_thresh

        if rsi_condition and fg_condition and funding_condition:
            # Scaled conviction based on severity of oversold and fear conditions
            depth_factor = min(1.0, max(0.0, (self.rsi_entry_thresh - rsi) / 15.0))
            fear_factor = min(1.0, max(0.0, (self.fg_thresh - fg_index) / 25.0))
            funding_factor = 0.2 if funding < 0.0 else 0.0

            confidence = round(min(0.95, max(0.55, 0.55 + 0.25 * depth_factor + 0.1 * fear_factor + funding_factor)), 2)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_oversold_long",
                    "rsi": round(rsi, 2),
                    "fear_greed_index": fg_index,
                    "funding_rate_ethusdt": funding,
                    "price": current_price,
                },
            )

        return None