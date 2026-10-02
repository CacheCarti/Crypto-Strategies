from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleConfluenceReversion(Strategy):
    METADATA = {
        "name": "Triple Confluence Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.rsi_oversold = 36.0
        self.rsi_exit = 58.0
        self.fg_max = 40.0
        self.funding_max = 0.00002
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _rsi(self, closes, period: int = 12) -> Optional[float]:
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
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 2:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        funding_rate = float(ctx.features.get("funding_rate_ethusdt", 0.0))
        has_pos = ctx.has_position()

        # Exit logic for open long position
        if has_pos:
            if ctx.position_direction() == "long":
                if rsi_val >= self.rsi_exit or funding_rate > 0.00035:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "confluence_target_reached_or_funding_heated",
                            "rsi": round(rsi_val, 2),
                            "fear_greed": fg_index,
                            "funding_rate": funding_rate,
                            "close_price": ctx.bar.close,
                        },
                    )
            return None

        # Entry logic: verify cooldown has elapsed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Triple edge stack check
        is_rsi_oversold = rsi_val <= self.rsi_oversold
        is_market_fear = fg_index <= self.fg_max
        is_funding_discounted = funding_rate <= self.funding_max

        if is_rsi_oversold and is_market_fear and is_funding_discounted:
            # Scale confidence with extreme conditions
            confidence = 0.70
            if rsi_val < 30.0:
                confidence += 0.10
            if fg_index < 25.0:
                confidence += 0.10
            if funding_rate < 0.0:
                confidence += 0.05
            confidence = min(0.95, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=640.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "triple_confluence_oversold_fear_discount",
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fg_index,
                    "funding_rate": funding_rate,
                    "close_price": ctx.bar.close,
                },
            )

        return None