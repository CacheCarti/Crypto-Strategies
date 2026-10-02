from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class RegimeGatedMomentum(Strategy):
    METADATA = {
        "name": "Regime Gated Volatility Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 170,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.vol_baseline_period = 168
        self.sma_period = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.vol_history: List[float] = []

    def _realized_vol(self, closes: List[float], period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                continue
            returns.append((closes[i] - prev) / prev)
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var)

    def _median(self, values: List[float]) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        mid = len(sorted_vals) // 2
        if len(sorted_vals) % 2 == 0:
            return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
        return sorted_vals[mid]

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.vol_baseline_period + self.vol_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < self.vol_baseline_period + self.vol_period:
            return None

        current_vol = self._realized_vol(closes, self.vol_period)
        if current_vol is None:
            return None

        self.vol_history.append(current_vol)
        if len(self.vol_history) > self.vol_baseline_period * 2:
            self.vol_history = self.vol_history[-self.vol_baseline_period:]

        if len(self.vol_history) < self.vol_baseline_period:
            return None

        vol_baseline = self._median(self.vol_history[-self.vol_baseline_period:])
        if vol_baseline <= 1e-8:
            return None

        vol_ratio = current_vol / vol_baseline
        price = ctx.bar.close
        sma = sum(closes[-self.sma_period:]) / float(self.sma_period)
        prev_sma = sum(closes[-self.sma_period - 1:-1]) / float(self.sma_period)

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Regime safety: avoid entering or holding in extreme crisis
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN") and has_pos:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "market_crisis_regime_exit",
                    "price": price,
                    "market_regime": market_regime,
                },
            )

        # Position exit management: give trades room, exit only on clear trend breakdown or vol collapse
        if has_pos:
            exit_reason = None
            if vol_ratio < 0.75:
                exit_reason = "volatility_collapsed_flat_regime"
            elif pos_dir == "long" and price < sma * 0.988:
                exit_reason = "long_trend_break_below_sma"
            elif pos_dir == "short" and price > sma * 1.012:
                exit_reason = "short_trend_break_above_sma"

            if exit_reason:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "price": price,
                        "sma": sma,
                        "vol_ratio": vol_ratio,
                        "current_vol": current_vol,
                        "vol_baseline": vol_baseline,
                    },
                )
            return None

        # Hard cooldown: wait mandatory multi-bar buffer before considering new entry
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry gating: volatility must be decisively higher than baseline median
        if vol_ratio < 1.25:
            return None

        # Confidence scales with volatility expansion (capped at 0.90)
        confidence = min(0.90, max(0.55, 0.55 + 0.20 * (vol_ratio - 1.25)))
        prev_close = closes[-2]
        prev2_close = closes[-3]

        # Long breakout: fresh breakout above SMA with consecutive positive price action
        bullish_breakout = (prev_close <= prev_sma * 1.002 or prev2_close <= prev_sma) and (price > sma * 1.005)
        bullish_continuation = price > sma * 1.008 and closes[-1] > closes[-2] > closes[-3]

        if (bullish_breakout or bullish_continuation) and price > sma * 1.005:
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "vol_expansion_long_momentum",
                    "price": price,
                    "sma": sma,
                    "vol_ratio": vol_ratio,
                    "current_vol": current_vol,
                    "vol_baseline": vol_baseline,
                },
            )

        # Short breakdown: fresh breakdown below SMA with consecutive negative price action
        bearish_breakdown = (prev_close >= prev_sma * 0.998 or prev2_close >= prev_sma) and (price < sma * 0.995)
        bearish_continuation = price < sma * 0.992 and closes[-1] < closes[-2] < closes[-3]

        if (bearish_breakdown or bearish_continuation) and price < sma * 0.995:
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "vol_expansion_short_momentum",
                    "price": price,
                    "sma": sma,
                    "vol_ratio": vol_ratio,
                    "current_vol": current_vol,
                    "vol_baseline": vol_baseline,
                },
            )

        return None