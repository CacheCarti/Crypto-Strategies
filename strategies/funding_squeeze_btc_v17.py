from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.atr_period = 14
        self.funding_threshold = 0.00003  # 0.003% bias
        self.min_return_bps = 70.0        # price move threshold
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.cooldown_bars = 6
        self.max_hold_bars = 16           # ~16 hours timed max hold

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
        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)

        if len(closes) < self.lookback + 1 or len(highs) < self.atr_period + 1:
            return None

        # Timed exit or structural momentum exit if holding
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based expiration to prevent drifting
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_squeeze_exhaustion", "bars_held": bars_held}
                )

            # Volatility trailing check: if opposing momentum accelerates, cut early
            recent_ret = (closes[-1] - closes[-4]) / closes[-4] * 10000.0 if len(closes) >= 5 else 0.0
            if pos_dir == "long" and recent_ret < -120.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_momentum_failure", "ret_bps": recent_ret})
            elif pos_dir == "short" and recent_ret > 120.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_momentum_failure", "ret_bps": recent_ret})

            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis filter from market context
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        past_price = closes[-self.lookback]
        curr_price = closes[-1]
        price_ret_bps = ((curr_price - past_price) / past_price) * 10000.0

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or curr_price <= 0:
            return None
        atr_bps = (atr / curr_price) * 10000.0

        # Dynamic volatility stop calibration
        dynamic_sl = max(180.0, min(380.0, atr_bps * 1.6))
        dynamic_tp = max(300.0, min(650.0, atr_bps * 3.0))

        # Long Squeeze: Price rising over 24h while perpetual funding is negative (shorts paying longs)
        # Shorts get trapped and forced to buy-to-cover as price extends higher
        if price_ret_bps > self.min_return_bps and funding < -self.funding_threshold:
            confidence = min(0.90, 0.60 + min(abs(price_ret_bps) / 400.0, 0.20) + min(abs(funding) * 500.0, 0.10))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding_rate": funding,
                    "price_ret_24h_bps": price_ret_bps,
                    "atr_bps": atr_bps,
                    "dynamic_sl": dynamic_sl
                }
            )

        # Short Squeeze: Price falling over 24h while perpetual funding is positive (longs paying shorts)
        # Longs get trapped and forced to liquidate as price falls
        if price_ret_bps < -self.min_return_bps and funding > self.funding_threshold:
            confidence = min(0.90, 0.60 + min(abs(price_ret_bps) / 400.0, 0.20) + min(funding * 500.0, 0.10))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "long_liquidation_squeeze",
                    "funding_rate": funding,
                    "price_ret_24h_bps": price_ret_bps,
                    "atr_bps": atr_bps,
                    "dynamic_sl": dynamic_sl
                }
            )

        return None