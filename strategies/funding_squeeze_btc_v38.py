from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeEventStudy(Strategy):
    METADATA = {
        "name": "FundingSqueezeEventStudy",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_period = 18
        self.last_exit_bar = -999
        self.min_return_pct = 0.012
        self.funding_long_thresh = -0.00003
        self.funding_short_thresh = 0.00008

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
        if len(closes) < self.lookback + 2:
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Exit/Flat logic if in position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            funding = ctx.features.get("funding_rate_btcusdt", 0.0)
            if pos_dir == "long" and funding > 0.00015:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "squeeze_exhausted_long", "funding": funding}
                )
            elif pos_dir == "short" and funding < -0.0001:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "squeeze_exhausted_short", "funding": funding}
                )
            return None

        # Market regime filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        cum_ret_24 = (current_close - past_close) / past_close

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        highs = ctx.highs(16)
        lows = ctx.lows(16)
        atr = self._atr(highs, lows, closes[-16:], period=14)
        if atr is None or atr <= 0:
            return None

        # Convert ATR to basis points for dynamic SL/TP
        atr_bps = (atr / current_close) * 10000.0
        sl_bps = max(160.0, min(300.0, atr_bps * 1.6))
        tp_bps = max(320.0, min(650.0, sl_bps * 2.0))

        # Event: Short Squeeze continuation
        # Price rising strongly over 24 bars while funding is sustained negative (shorts trapped)
        if cum_ret_24 > self.min_return_pct and funding < self.funding_long_thresh:
            confidence = min(0.9, 0.6 + abs(funding) * 2000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_short_squeeze_continuation",
                    "cum_ret_24": round(cum_ret_24, 4),
                    "funding": funding,
                    "price": current_close,
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                }
            )

        # Event: Long Squeeze (Long liquidation cascade)
        # Price falling strongly over 24 bars while funding stays heavily positive (longs trapped)
        if cum_ret_24 < -self.min_return_pct and funding > self.funding_short_thresh:
            confidence = min(0.9, 0.6 + funding * 1500.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_long_squeeze_breakdown",
                    "cum_ret_24": round(cum_ret_24, 4),
                    "funding": funding,
                    "price": current_close,
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                }
            )

        return None