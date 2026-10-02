from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolEmaRibbonTrend(Strategy):
    METADATA = {
        "name": "SOL EMA Ribbon Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 760.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.mid_period = 25
        self.slow_period = 60
        self.min_spread_bps = 75.0
        self.min_gap_bps = 25.0
        self.cooldown_bars = 10
        self.last_exit_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 25)
        if len(closes) < self.slow_period + 1:
            return None

        # Filter out extreme crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "crisis_regime_exit", "regime": regime},
                )
            return None

        current_price = ctx.bar.close

        ema_fast = self._ema(closes, self.fast_period)
        ema_mid = self._ema(closes, self.mid_period)
        ema_slow = self._ema(closes, self.slow_period)

        if ema_fast is None or ema_mid is None or ema_slow is None:
            return None

        total_spread_bps = (abs(ema_fast - ema_slow) / current_price) * 10000.0
        fast_mid_gap_bps = (abs(ema_fast - ema_mid) / current_price) * 10000.0
        mid_slow_gap_bps = (abs(ema_mid - ema_slow) / current_price) * 10000.0

        # Position exit management
        if ctx.has_position():
            direction = ctx.position_direction()

            if direction == "long":
                # Exit when fast EMA crosses below mid EMA or price closes significantly below mid EMA
                if ema_fast <= ema_mid or current_price < (ema_mid * 0.995):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_ribbon_structure_broken",
                            "ema_fast": round(ema_fast, 3),
                            "ema_mid": round(ema_mid, 3),
                            "ema_slow": round(ema_slow, 3),
                            "price": round(current_price, 3),
                            "spread_bps": round(total_spread_bps, 1),
                        },
                    )

            elif direction == "short":
                # Exit when fast EMA crosses above mid EMA or price closes significantly above mid EMA
                if ema_fast >= ema_mid or current_price > (ema_mid * 1.005):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_ribbon_structure_broken",
                            "ema_fast": round(ema_fast, 3),
                            "ema_mid": round(ema_mid, 3),
                            "ema_slow": round(ema_slow, 3),
                            "price": round(current_price, 3),
                            "spread_bps": round(total_spread_bps, 1),
                        },
                    )

            return None

        # Hard multi-bar cooldown after every exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out tight / unexpanded ribbons
        if total_spread_bps < self.min_spread_bps:
            return None

        # Require meaningful spacing between consecutive ribbons to ensure clear trend structure
        if fast_mid_gap_bps < self.min_gap_bps or mid_slow_gap_bps < self.min_gap_bps:
            return None

        # Dynamic confidence based on ribbon strength
        confidence = min(0.85, max(0.50, 0.50 + (total_spread_bps / 300.0) * 0.35))

        # Bullish alignment: 10 > 25 > 60 and price comfortably above fast EMA
        if ema_fast > ema_mid > ema_slow and current_price >= ema_fast:
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=380.0,
                take_profit_bps=760.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "bullish_ema_ribbon_expansion",
                    "ema_fast": round(ema_fast, 3),
                    "ema_mid": round(ema_mid, 3),
                    "ema_slow": round(ema_slow, 3),
                    "price": round(current_price, 3),
                    "spread_bps": round(total_spread_bps, 1),
                    "regime": regime,
                },
            )

        # Bearish alignment: 10 < 25 < 60 and price comfortably below fast EMA
        if ema_fast < ema_mid < ema_slow and current_price <= ema_fast:
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=380.0,
                take_profit_bps=760.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "bearish_ema_ribbon_expansion",
                    "ema_fast": round(ema_fast, 3),
                    "ema_mid": round(ema_mid, 3),
                    "ema_slow": round(ema_slow, 3),
                    "price": round(current_price, 3),
                    "spread_bps": round(total_spread_bps, 1),
                    "regime": regime,
                },
            )

        return None