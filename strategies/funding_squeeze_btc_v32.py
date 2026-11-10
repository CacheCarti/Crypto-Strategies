from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeRegimeBTC(Strategy):
    METADATA = {
        "name": "FundingSqueezeRegimeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 28800,  # ~8 hours typical squeeze expansion
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 14
        self.last_trade_bar = -999
        self.funding_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 6)
        highs = ctx.highs(self.lookback + 6)
        lows = ctx.lows(self.lookback + 6)
        
        if len(closes) < self.lookback + 1:
            return None

        # Track funding history
        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 40:
            self.funding_history.pop(0)

        # Respect trade cooldown to keep total trades within 15-70 bounds
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime gating: Prior failure analysis shows squeeze continuation fails during extreme crisis / meltdown.
        # Only trade in NORMAL or non-crisis environments.
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate 24-bar price return
        start_price = closes[-self.lookback]
        current_price = closes[-1]
        price_return_pct = (current_price - start_price) / start_price

        # Check funding persistence over the lookback window
        if len(self.funding_history) < self.lookback:
            return None

        recent_funding = self.funding_history[-self.lookback:]
        avg_funding = sum(recent_funding) / len(recent_funding)

        # Baseline trend filter via EMA
        ema_trend = self._ema(closes, 30)
        if ema_trend is None:
            return None

        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / current_price) if atr else 0.01

        # DIVERGENCE SETUP 1: Short Squeeze continuation
        # Price has pushed UP by at least 1.2%, but funding remains negative (shorts piling in against the push)
        short_squeeze = (
            price_return_pct >= 0.012 and
            avg_funding <= -0.00003 and
            current_funding <= 0.0 and
            current_price > ema_trend
        )

        # DIVERGENCE SETUP 2: Long Squeeze (liquidation cascade) continuation
        # Price has pushed DOWN by at least 1.2%, but funding remains stubbornly positive (longs catching falling knives)
        long_squeeze = (
            price_return_pct <= -0.012 and
            avg_funding >= 0.00003 and
            current_funding >= 0.0 and
            current_price < ema_trend
        )

        if short_squeeze and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.90, max(0.60, 0.65 + abs(avg_funding) * 5000.0))
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "short_squeeze_continuation_negative_funding",
                    "price_ret_24": round(price_return_pct, 4),
                    "avg_funding": round(avg_funding, 6),
                    "current_funding": round(current_funding, 6),
                    "ema30": round(ema_trend, 2),
                    "atr_pct": round(atr_pct, 4),
                }
            )

        if long_squeeze and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.90, max(0.60, 0.65 + abs(avg_funding) * 5000.0))
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "long_squeeze_continuation_positive_funding",
                    "price_ret_24": round(price_return_pct, 4),
                    "avg_funding": round(avg_funding, 6),
                    "current_funding": round(current_funding, 6),
                    "ema30": round(ema_trend, 2),
                    "atr_pct": round(atr_pct, 4),
                }
            )

        # Exit management: if divergence resolves (funding flips aggressively in price direction), signal flat
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and avg_funding > 0.00015:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.70, metadata={"reason": "squeeze_exhaustion_funding_positive", "avg_funding": round(avg_funding, 6)})
            elif direction == "short" and avg_funding < -0.00015:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.70, metadata={"reason": "squeeze_exhaustion_funding_negative", "avg_funding": round(avg_funding, 6)})

        return None