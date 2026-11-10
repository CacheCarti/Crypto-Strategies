from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeEventBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeEventBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # 8 hours typical squeeze horizon
        "warmup_bars": 36,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 18
        self.last_exit_bar = -100
        self.funding_history = []

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 2:
            return None

        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.lookback + 5:
            self.funding_history.pop(0)

        # Enforce cooldown window after any position exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Manage existing active position
        if ctx.has_position():
            pos = ctx.position
            direction = pos.get("direction")
            entry_price = pos.get("entry_price", closes[-1])
            ret_bps = ((closes[-1] - entry_price) / entry_price) * 10000.0 if direction == "long" else ((entry_price - closes[-1]) / entry_price) * 10000.0
            
            # Squeeze exhaustion / normalization exit
            if direction == "long" and current_funding > 0.00015:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "funding_normalized_long_exit", "funding": current_funding, "ret_bps": ret_bps})
            if direction == "short" and current_funding < -0.0001:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "funding_normalized_short_exit", "funding": current_funding, "ret_bps": ret_bps})
            return None

        # Market regime filter: avoid illiquid/crisis breakdown noise
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.65:
            return None

        if len(self.funding_history) < self.lookback:
            return None

        # Price momentum over event window
        price_ret = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]
        
        # Funding rate persistence over event window
        recent_funding = self.funding_history[-self.lookback:]
        avg_funding = sum(recent_funding) / len(recent_funding)
        neg_funding_count = sum(1 for f in recent_funding if f < -0.00002)
        pos_funding_count = sum(1 for f in recent_funding if f > 0.00005)

        # Event trigger 1: Short Squeeze
        # Price is up significantly over 24h (>1.2%) while funding stayed consistently negative
        is_short_squeeze = (price_ret > 0.012) and (avg_funding < -0.00003) and (neg_funding_count >= 16) and (closes[-1] > closes[-2])

        # Event trigger 2: Long Flush / Squeeze Down
        # Price is down significantly over 24h (<-1.2%) while funding stayed stubbornly positive
        is_long_flush = (price_ret < -0.012) and (avg_funding > 0.00006) and (pos_funding_count >= 16) and (closes[-1] < closes[-2])

        highs = ctx.highs(16)
        lows = ctx.lows(16)
        atr = self._atr(highs, lows, closes[-16:], period=14)
        atr_pct = (atr / closes[-1]) if (atr and closes[-1] > 0) else 0.005

        if is_short_squeeze:
            sl = max(160.0, min(350.0, atr_pct * 1.5 * 10000.0))
            tp = max(350.0, min(700.0, sl * 2.1))
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=sl,
                take_profit_bps=tp,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_ret_24h": round(price_ret * 100, 2),
                    "avg_funding": avg_funding,
                    "neg_count": neg_funding_count,
                    "close": closes[-1]
                }
            )

        if is_long_flush:
            sl = max(160.0, min(350.0, atr_pct * 1.5 * 10000.0))
            tp = max(350.0, min(700.0, sl * 2.1))
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=sl,
                take_profit_bps=tp,
                metadata={
                    "reason": "long_liquidation_flush",
                    "price_ret_24h": round(price_ret * 100, 2),
                    "avg_funding": avg_funding,
                    "pos_count": pos_funding_count,
                    "close": closes[-1]
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index