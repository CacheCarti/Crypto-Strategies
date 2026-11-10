from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_period = 12
        self.last_exit_bar = -100
        self.funding_history = []
        self.min_return_pct = 1.2
        self.funding_threshold = 0.00005

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
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > 100:
            self.funding_history.pop(0)

        if ctx.bar_index < self.METADATA["warmup_bars"] or len(self.funding_history) < self.lookback:
            return None

        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        price_ret_pct = ((current_close - past_close) / past_close) * 100.0

        recent_funding = self.funding_history[-self.lookback:]
        avg_funding = sum(recent_funding) / len(recent_funding)

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            pos_dict = ctx.position or {}
            entry_price = pos_dict.get("entry_price", current_close)
            
            if pos_dir == "long":
                pnl_bps = ((current_close - entry_price) / entry_price) * 10000.0
                if avg_funding > self.funding_threshold or pnl_bps <= -self.METADATA["declared_sl_bps"]:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_exhaustion_or_sl", "avg_funding": avg_funding, "pnl_bps": pnl_bps})
            elif pos_dir == "short":
                pnl_bps = ((entry_price - current_close) / entry_price) * 10000.0
                if avg_funding < -self.funding_threshold or pnl_bps <= -self.METADATA["declared_sl_bps"]:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_exhaustion_or_sl", "avg_funding": avg_funding, "pnl_bps": pnl_bps})
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        recent_closes = ctx.closes(20)
        atr_val = self._atr(highs, lows, recent_closes, 14)
        if atr_val is None or current_close == 0:
            return None
        atr_bps = (atr_val / current_close) * 10000.0
        if atr_bps < 15.0 or atr_bps > 180.0:
            return None

        # Short squeeze continuation: price rallying while shorts pay funding
        if price_ret_pct >= self.min_return_pct and avg_funding < -self.funding_threshold:
            conf = min(0.9, 0.6 + abs(avg_funding) * 2000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_short_squeeze_continuation",
                    "price_ret_24h": round(price_ret_pct, 2),
                    "avg_funding_24h": round(avg_funding, 6),
                    "atr_bps": round(atr_bps, 1),
                }
            )

        # Long squeeze breakdown: price falling while longs pay funding
        if price_ret_pct <= -self.min_return_pct and avg_funding > self.funding_threshold:
            conf = min(0.9, 0.6 + abs(avg_funding) * 2000.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_long_squeeze_breakdown",
                    "price_ret_24h": round(price_ret_pct, 2),
                    "avg_funding_24h": round(avg_funding, 6),
                    "atr_bps": round(atr_bps, 1),
                }
            )

        return None