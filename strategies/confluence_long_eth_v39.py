from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.max_hold_bars = 24

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
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # In-position management
        if ctx.has_position():
            pos = ctx.position
            bars_held = 0
            if pos and "entry_price" in pos:
                bars_held = ctx.bar_index - pos.get("entry_bar", ctx.bar_index)
            
            # Explicit event boundary exit: mean-reversion achieved or time stop reached
            if current_rsi >= 50.0 or funding >= 0.00015 or bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "confluence_normalization_or_time_exit",
                        "rsi": round(current_rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held,
                        "fg_index": fg_index
                    }
                )
            return None

        # Hard cooldown enforcement between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Event criteria: Triple edge alignment
        # 1. Crowd heavily shorting (negative funding)
        # 2. Market-wide panic sentiment (Fear & Greed < 35)
        # 3. Price-action exhaustion / oversold bounce setup (RSI < 35)
        funding_condition = funding < 0.0
        fg_condition = fg_index < 35.0
        rsi_condition = current_rsi < 35.0

        if funding_condition and fg_condition and rsi_condition:
            # Scale confidence by confluence magnitude
            conf = 0.70
            if current_rsi < 28.0:
                conf += 0.15
            if funding < -0.0001:
                conf += 0.10
            conf = min(conf, 0.95)

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fg_rsi",
                    "rsi": round(current_rsi, 2),
                    "funding_rate": round(funding, 6),
                    "fear_greed": round(fg_index, 1),
                    "price": ctx.bar.close
                }
            )

        return None