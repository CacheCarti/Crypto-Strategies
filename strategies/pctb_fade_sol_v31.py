from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeStrategy(Strategy):
    METADATA = {
        "name": "SolPctBFadeStrategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Calculate current Bollinger Bands
        bb_curr = self._bollinger(closes[:-0] if False else closes, self.period, self.num_std)
        # Calculate previous bar's Bollinger Bands
        bb_prev = self._bollinger(closes[:-1], self.period, self.num_std)
        if bb_curr is None or bb_prev is None:
            return None

        mid_curr, upper_curr, lower_curr = bb_curr
        mid_prev, upper_prev, lower_prev = bb_prev

        band_width_curr = upper_curr - lower_curr
        band_width_prev = upper_prev - lower_prev

        if band_width_curr <= 0 or band_width_prev <= 0:
            return None

        c_curr = closes[-1]
        c_prev = closes[-2]

        pct_b_curr = (c_curr - lower_curr) / band_width_curr
        pct_b_prev = (c_prev - lower_prev) / band_width_prev

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and c_curr >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_midband_touch_long", "pct_b": pct_b_curr, "mid": mid_curr, "close": c_curr}
                )
            elif direction == "short" and c_curr <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_midband_touch_short", "pct_b": pct_b_curr, "mid": mid_curr, "close": c_curr}
                )
            return None

        # Mandatory cooldown check after last trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid strong trending runaways / crisis regimes where mean-reversion fails
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Relative band width filter: avoid trading ultra-tight compressions right before explosive breakouts
        bandwidth_ratio = band_width_curr / mid_curr
        if bandwidth_ratio < 0.015:
            return None

        # LONG setup: Previous bar pierced below lower band (pct_b < 0.0), current bar closes back inside (0.0 <= pct_b <= 0.35)
        # Filter against entering longs during confirmed strong bear regimes
        if pct_b_prev < 0.0 and 0.0 <= pct_b_curr <= 0.35 and trend_regime != "bear":
            confidence = 0.75 if trend_regime == "bull" else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_break_below_lower_band",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "lower_band": lower_curr,
                    "close": c_curr,
                    "bandwidth_ratio": bandwidth_ratio,
                    "trend_regime": trend_regime
                }
            )

        # SHORT setup: Previous bar pierced above upper band (pct_b > 1.0), current bar closes back inside (0.65 <= pct_b <= 1.0)
        # Filter against entering shorts during confirmed strong bull regimes
        if pct_b_prev > 1.0 and 0.65 <= pct_b_curr <= 1.0 and trend_regime != "bull":
            confidence = 0.75 if trend_regime == "bear" else 0.65
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_break_above_upper_band",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "upper_band": upper_curr,
                    "close": c_curr,
                    "bandwidth_ratio": bandwidth_ratio,
                    "trend_regime": trend_regime
                }
            )

        return None