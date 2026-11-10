from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "FundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 18

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Check ongoing position for time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_hold_reached", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis regime
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate 24-bar price return
        price_now = closes[-1]
        price_24_ago = closes[-self.lookback - 1]
        price_return_bps = ((price_now - price_24_ago) / price_24_ago) * 10000.0

        # Funding rate (feature updates hourly on btc_usdc)
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Baseline trend filter via EMA
        ema_trend = self._ema(closes, 20)
        if ema_trend is None:
            return None

        # Short Squeeze setup:
        # Price is breaking upward over 24h (+100 bps) and above EMA, while funding remains negative (< -0.00002)
        if price_return_bps > 100.0 and price_now > ema_trend and funding < -0.00002:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + abs(funding) * 500.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_divergence",
                    "price_return_bps": round(price_return_bps, 1),
                    "funding": round(funding, 6),
                    "ema20": round(ema_trend, 2),
                    "price": round(price_now, 2)
                }
            )

        # Long Squeeze (Long liquidation cascade) setup:
        # Price is breaking downward over 24h (-100 bps) and below EMA, while funding remains positive (> 0.00005)
        if price_return_bps < -100.0 and price_now < ema_trend and funding > 0.00005:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + funding * 500.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_squeeze_divergence",
                    "price_return_bps": round(price_return_bps, 1),
                    "funding": round(funding, 6),
                    "ema20": round(ema_trend, 2),
                    "price": round(price_now, 2)
                }
            )

        return None