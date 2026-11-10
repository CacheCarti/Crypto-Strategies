from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold for squeeze unwinds
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.funding_lookback = 12
        self.price_lookback = 24
        self.min_move_pct = 0.012  # 1.2% price move over lookback

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_lookback + 2)
        if len(closes) < self.price_lookback + 1:
            return None

        # Mandatory post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        ref_price = closes[-self.price_lookback]
        price_return = (current_price - ref_price) / ref_price

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Volatility-scaled dynamic stops using ATR
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr_val = self._atr(highs, lows, closes[-20:], period=14)
        if atr_val is None or current_price <= 0:
            return None

        atr_bps = (atr_val / current_price) * 10000.0
        # Adapt stops to prevailing volatility
        sl_bps = max(180.0, min(360.0, atr_bps * 1.8))
        tp_bps = max(300.0, min(650.0, atr_bps * 3.2))

        # Check existing position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Invalidation exit: funding flips violently in favor of current price, relieving the squeeze
            if pos_dir == "long" and funding > 0.0003:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "squeeze_exhaustion_funding_positive", "funding": funding})
            elif pos_dir == "short" and funding < -0.0003:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "squeeze_exhaustion_funding_negative", "funding": funding})
            return None

        # Market regime filter: avoid choppy crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long Squeeze: Price actively advancing while aggregate funding is persistently negative (shorts trapped)
        if price_return >= self.min_move_pct and funding < -0.00005:
            conf = min(0.9, 0.6 + abs(funding) * 500.0 + (price_return * 5.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_funding_squeeze_continuation",
                    "funding_rate": funding,
                    "price_return_24h": round(price_return, 4),
                    "atr_bps": round(atr_bps, 1),
                    "price": current_price
                }
            )

        # Short Squeeze: Price actively declining while funding is persistently positive (longs trapped)
        if price_return <= -self.min_move_pct and funding > 0.00010:
            conf = min(0.9, 0.6 + (funding * 400.0) + (abs(price_return) * 5.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_funding_squeeze_continuation",
                    "funding_rate": funding,
                    "price_return_24h": round(price_return, 4),
                    "atr_bps": round(atr_bps, 1),
                    "price": current_price
                }
            )

        return None