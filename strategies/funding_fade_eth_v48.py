from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.funding_history = []
        self.max_funding_window = 10
        self.persistence_threshold = 3
        # Thresholds: ~0.015% per 8h is an extreme tilt on ETH
        self.extreme_pos_funding = 0.00015
        self.extreme_neg_funding = -0.00015
        self.neutral_band = 0.00005

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.max_funding_window:
            self.funding_history.pop(0)

        # Respect cooldown after exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate indicators
        ema20 = self._ema(closes, 20)
        highs = ctx.highs(15)
        lows = ctx.lows(15)
        atr14 = self._atr(highs, lows, closes, 14)

        if ema20 is None or atr14 is None:
            return None

        price = ctx.bar.close
        atr_pct = (atr14 / price) if price > 0 else 0.01
        # Dynamic volatility-adjusted SL/TP
        sl_bps = max(160.0, min(350.0, atr_pct * 1.5 * 10000.0))
        tp_bps = sl_bps * 1.7

        # Check existing position for normalization exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Exit when funding normalizes towards zero
            if pos_dir == "long" and current_funding >= -self.neutral_band:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_exit_long",
                    "funding": current_funding,
                    "price": price
                })
            elif pos_dir == "short" and current_funding <= self.neutral_band:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_exit_short",
                    "funding": current_funding,
                    "price": price
                })
            return None

        # Check persistence of extreme funding
        recent_readings = self.funding_history[-self.persistence_threshold:]
        if len(recent_readings) < self.persistence_threshold:
            return None

        all_extreme_negative = all(f <= self.extreme_neg_funding for f in recent_readings)
        all_extreme_positive = all(f >= self.extreme_pos_funding for f in recent_readings)

        # Regime and trend safety filters:
        # Don't catch falling knives: for longs, require price not completely broken (< 2.5% below EMA20)
        # and not in crisis meltdown
        crisis = ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]
        if crisis:
            return None

        dist_from_ema = (price - ema20) / ema20

        # Long Squeeze Setup: crowd heavily short + paying funding, price stabilizing near/above EMA or mild pullback
        if all_extreme_negative and dist_from_ema > -0.025:
            conf = min(0.9, 0.65 + abs(current_funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                metadata={
                    "reason": "persistent_negative_funding_squeeze",
                    "funding": current_funding,
                    "dist_from_ema": round(dist_from_ema, 4),
                    "atr_bps": round(atr_pct * 10000.0, 1),
                    "price": price
                }
            )

        # Short Squeeze/Fade Setup: crowd heavily long + overextended, price not in vertical parabolic breakout (> 2.5% above EMA)
        if all_extreme_positive and dist_from_ema < 0.025:
            conf = min(0.9, 0.65 + current_funding * 1000.0)
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                metadata={
                    "reason": "persistent_positive_funding_exhaustion",
                    "funding": current_funding,
                    "dist_from_ema": round(dist_from_ema, 4),
                    "atr_bps": round(atr_pct * 10000.0, 1),
                    "price": price
                }
            )

        return None