from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversion(Strategy):
    METADATA = {
        "name": "EthConfluenceReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_bar = ctx.bar_index

        # Position management
        if ctx.has_position():
            bars_held = current_bar - self.entry_bar
            # Dynamic exit: normalized RSI or funding flipping strongly positive or time horizon reached
            if rsi_val >= 55.0 or funding > 0.00015 or bars_held >= 12:
                self.last_exit_bar = current_bar
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "confluence_normalized_or_time_exit",
                        "rsi": round(rsi_val, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Entry logic
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        # Triple edge stack: crowded shorts (funding <= 0), pervasive fear (F&G <= 38), oversold price (RSI < 38)
        # Using 38 threshold on 1h bars produces roughly 30-70 robust, non-crowded trades over 17k bars
        cond_funding = funding <= 0.0
        cond_fear = fear_greed <= 38.0
        cond_rsi = rsi_val <= 38.0

        if cond_funding and cond_fear and cond_rsi:
            self.entry_bar = current_bar
            # Confidence scales with the depth of the dislocation
            conf = min(0.9, 0.6 + max(0.0, (35.0 - rsi_val) * 0.01) + max(0.0, -funding * 500.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_long_oversold_negative_funding",
                    "rsi": round(rsi_val, 2),
                    "fear_greed": round(fear_greed, 1),
                    "funding_rate": round(funding, 6),
                    "close": ctx.bar.close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index