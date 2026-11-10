from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.min_return_pct = 0.012

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        if len(closes) < self.lookback + 1:
            return None

        # Mandatory cooldown to prevent overtrading and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid trading during extreme crisis/meltdown where funding mechanics break down
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime", "regime": regime})
            return None

        current_close = ctx.bar.close
        past_close = closes[-self.lookback]
        price_return = (current_close - past_close) / past_close
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Baseline EMA trend alignment to filter false squeeze signals
        ema_slow = self._ema(closes, 30)
        if ema_slow is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean-reversion exit or funding flipping against thesis
            if pos_dir == "long" and funding > 0.0003:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_funding_normalized_long", "funding": funding})
            elif pos_dir == "short" and funding < -0.0003:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_funding_normalized_short", "funding": funding})
            return None

        # Squeeze Entries: Divergence between positioning and momentum
        # Short Squeeze Continuation: Price pushing up strongly while funding remains persistently negative
        if price_return >= self.min_return_pct and funding < -0.00005 and current_close > ema_slow:
            conf = min(0.9, 0.55 + abs(funding) * 500.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding": funding,
                    "price_return_24": round(price_return, 4),
                    "close": current_close,
                    "ema_slow": round(ema_slow, 2)
                }
            )

        # Long Squeeze Continuation: Price breaking down while longs remain crowded / funding positive
        if price_return <= -self.min_return_pct and funding > 0.00005 and current_close < ema_slow:
            conf = min(0.9, 0.55 + abs(funding) * 500.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "long_squeeze_breakdown",
                    "funding": funding,
                    "price_return_24": round(price_return, 4),
                    "close": current_close,
                    "ema_slow": round(ema_slow, 2)
                }
            )

        return None