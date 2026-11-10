from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Exit logic if currently holding long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Normalize exit: crowd no longer shorting, fear dissipating, or RSI fully rebounded
            if current_rsi >= 55.0 or funding > 0.0001 or bars_held >= 24:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "confluence_decay_or_target_met",
                        "rsi": round(current_rsi, 2),
                        "funding": round(funding, 6),
                        "fear_greed": round(fg_index, 1),
                        "bars_held": bars_held,
                    },
                )
            return None

        # Guard: enforce cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry logic: high-conviction triple confluence
        # 1. Crowd heavily shorting (negative funding rate)
        # 2. Macro sentiment in fear territory (< 35)
        # 3. Micro price deeply oversold (RSI < 35)
        if funding < 0.0 and fg_index <= 35.0 and current_rsi <= 35.0:
            # Scaled confidence based on depth of oversold/negative funding
            depth_bonus = min(0.3, max(0.0, (35.0 - current_rsi) / 50.0))
            funding_bonus = min(0.2, max(0.0, -funding * 1000.0))
            conf = min(0.95, 0.65 + depth_bonus + funding_bonus)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_stack_confluence_oversold_fear_neg_funding",
                    "rsi": round(current_rsi, 2),
                    "funding_rate": round(funding, 6),
                    "fear_greed": round(fg_index, 1),
                    "price": ctx.bar.close,
                },
            )

        return None