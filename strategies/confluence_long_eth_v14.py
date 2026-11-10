from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversal(Strategy):
    METADATA = {
        "name": "EthConfluenceReversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_trade = 0

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
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Handle active position management
        if ctx.has_position():
            self.bars_in_trade += 1
            # Normalization exit: RSI rebounded or funding flipped strongly positive
            should_exit = False
            exit_reason = ""

            if rsi >= 55.0:
                should_exit = True
                exit_reason = "rsi_normalized_above_55"
            elif funding > 0.00015:
                should_exit = True
                exit_reason = "crowd_flipped_long_funding"
            elif self.bars_in_trade >= 20:
                should_exit = True
                exit_reason = "max_hold_reached"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                self.bars_in_trade = 0
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "fear_greed": fg_index,
                        "bars_held": self.bars_in_trade,
                    },
                )
            return None

        # Cooldown guard to avoid rapid re-entries and fee bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry criteria: crowd shorting + fearful market + local price exhaustion
        crowd_short = funding <= -0.000005
        market_fear = fg_index <= 38.0
        oversold = rsi <= 36.0

        if crowd_short and market_fear and oversold:
            # Scaled confidence based on depth of oversold & fear readings
            conviction = 0.65
            if rsi <= 28.0:
                conviction += 0.15
            if funding < -0.0001:
                conviction += 0.10
            if fg_index < 25.0:
                conviction += 0.10
            confidence = min(round(conviction, 2), 0.95)

            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(rsi, 2),
                    "funding": funding,
                    "fear_greed": fg_index,
                    "price": ctx.bar.close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0