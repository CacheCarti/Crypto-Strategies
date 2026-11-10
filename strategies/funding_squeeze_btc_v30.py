from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.min_return_pct = 1.2
        self.funding_threshold = 0.00003

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

        current_bar = ctx.bar_index
        if (current_bar - self.last_exit_bar) < self.cooldown_bars:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        curr_price = closes[-1]
        prior_price = closes[-self.lookback]
        rolling_ret_pct = ((curr_price - prior_price) / prior_price) * 100.0

        prev_ret_pct = ((closes[-2] - closes[-self.lookback - 1]) / closes[-self.lookback - 1]) * 100.0

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes[-20:], 14)
        if atr is None or atr == 0:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        highest_prior = max(ctx.highs(self.lookback)[:-1])
        lowest_prior = min(ctx.lows(self.lookback)[:-1])

        long_squeeze_boundary = (
            rolling_ret_pct >= self.min_return_pct
            and prev_ret_pct < self.min_return_pct
            and funding < -self.funding_threshold
            and curr_price > highest_prior
        )

        short_squeeze_boundary = (
            rolling_ret_pct <= -self.min_return_pct
            and prev_ret_pct > -self.min_return_pct
            and funding > self.funding_threshold
            and curr_price < lowest_prior
        )

        if not ctx.has_position():
            if long_squeeze_boundary:
                confidence = min(0.9, 0.55 + abs(funding) * 5000.0)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "negative_funding_upward_breakout",
                        "rolling_ret_pct": round(rolling_ret_pct, 2),
                        "funding": round(funding, 6),
                        "breakout_level": round(highest_prior, 1),
                        "price": round(curr_price, 1),
                    },
                )
            elif short_squeeze_boundary:
                confidence = min(0.9, 0.55 + abs(funding) * 5000.0)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "positive_funding_downward_breakdown",
                        "rolling_ret_pct": round(rolling_ret_pct, 2),
                        "funding": round(funding, 6),
                        "breakdown_level": round(lowest_prior, 1),
                        "price": round(curr_price, 1),
                    },
                )
        else:
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and funding > (self.funding_threshold * 2):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "squeeze_exhausted_funding_flipped", "funding": round(funding, 6)},
                )
            elif pos_dir == "short" and funding < (-self.funding_threshold * 2):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "squeeze_exhausted_funding_flipped", "funding": round(funding, 6)},
                )

        return None