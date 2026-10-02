from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ETHTripleConfluenceSwing(Strategy):
    METADATA = {
        "name": "ETH Triple Confluence Swing",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.rsi_oversold = 36.0
        self.rsi_exit = 58.0
        self.fg_fear_threshold = 40.0
        self.funding_max_threshold = 0.00003
        self.cooldown_bars = 6
        self.max_hold_bars = 24
        self.last_exit_bar = -100
        self.entry_bar = -1

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
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50.0)
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            if self.entry_bar < 0:
                self.entry_bar = ctx.bar_index

            bars_held = ctx.bar_index - self.entry_bar

            # Normalization exits
            if rsi >= self.rsi_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_reversion_target_reached",
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held,
                        "exit_price": current_price
                    }
                )

            if fg_index > 65.0:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "fear_greed_normalized_to_greed",
                        "fear_greed": fg_index,
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held
                    }
                )

            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.50,
                    metadata={
                        "reason": "max_hold_horizon_reached",
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held
                    }
                )

            return None

        # Entry logic: triple confluence filter + cooldown gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        is_oversold = rsi <= self.rsi_oversold
        is_fear = fg_index <= self.fg_fear_threshold
        is_favorable_funding = funding <= self.funding_max_threshold

        if is_oversold and is_fear and is_favorable_funding:
            # Scale confidence based on depth of oversold & fear
            conf = 0.65
            if rsi < 30.0:
                conf += 0.15
            if fg_index < 30.0:
                conf += 0.10
            confidence = min(0.95, conf)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "triple_confluence_rsi_fear_funding",
                    "rsi": round(rsi, 2),
                    "fear_greed": fg_index,
                    "funding_rate": funding,
                    "entry_price": current_price,
                    "bar_index": ctx.bar_index
                }
            )

        return None