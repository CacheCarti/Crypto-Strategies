from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeEvent(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeEvent",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.min_ret_threshold = 0.012

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
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if ctx.has_position():
            return None

        # Check market regime to avoid entering during extreme systemic meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.75:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        price_now = closes[-1]
        price_past = closes[-self.lookback]
        cum_ret = (price_now - price_past) / price_past

        # Dynamic confirmation via volatility
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_pct = atr / price_now

        # Short squeeze continuation: Strong upward price move while funding remains negative (shorts trapped & paying/adding)
        long_condition = (cum_ret >= max(self.min_ret_threshold, atr_pct * 1.5)) and (funding <= -0.00005)

        # Long squeeze breakdown: Strong downward price move while funding remains positive (longs trapped & underwater)
        short_condition = (cum_ret <= -max(self.min_ret_threshold, atr_pct * 1.5)) and (funding >= 0.00010)

        if long_condition:
            conf = min(0.9, 0.6 + abs(cum_ret) * 5.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation_negative_funding",
                    "cum_ret": round(cum_ret, 4),
                    "funding": round(funding, 6),
                    "atr_pct": round(atr_pct, 4),
                    "price": round(price_now, 2),
                }
            )

        if short_condition:
            conf = min(0.9, 0.6 + abs(cum_ret) * 5.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_squeeze_breakdown_positive_funding",
                    "cum_ret": round(cum_ret, 4),
                    "funding": round(funding, 6),
                    "atr_pct": round(atr_pct, 4),
                    "price": round(price_now, 2),
                }
            )

        return None