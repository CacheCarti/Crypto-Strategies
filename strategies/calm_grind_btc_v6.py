from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class CalmRegimeCarry(Strategy):
    METADATA = {
        "name": "Calm Regime Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 70,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_window = 24
        self.baseline_window = 60
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _calc_rv(self, closes: list) -> float:
        if len(closes) < 2:
            return 0.0
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
        if not returns:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.baseline_window + self.rv_window
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Calculate rolling RV samples to establish baseline median
        rv_samples = []
        step = max(1, self.baseline_window // 15)
        for i in range(0, self.baseline_window, step):
            end_idx = len(closes) - self.baseline_window + i + self.rv_window
            start_idx = end_idx - self.rv_window
            sub = closes[start_idx:end_idx]
            rv_samples.append(self._calc_rv(sub))

        current_rv = self._calc_rv(closes[-self.rv_window:])
        median_rv = statistics.median(rv_samples) if rv_samples else current_rv
        rv_ratio = (current_rv / median_rv) if median_rv > 0 else 1.0

        trend_regime = ctx.market.get("trend_regime", "neutral")
        market_regime = ctx.market.get("regime", "NORMAL")
        current_close = ctx.bar.close

        has_pos = ctx.has_position()

        if has_pos:
            vol_spike = rv_ratio > 1.35
            bear_flip = trend_regime == "bear"
            regime_stress = market_regime in ("CRISIS", "MELTDOWN")

            if vol_spike or bear_flip or regime_stress:
                self.last_exit_bar = ctx.bar_index
                reason = "vol_spike" if vol_spike else ("bear_regime" if bear_flip else "crisis_regime")
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": f"calm_carry_exit_{reason}",
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "rv_ratio": round(rv_ratio, 3),
                        "trend_regime": trend_regime,
                        "market_regime": market_regime,
                        "price": current_close,
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Favorable calm carry conditions: tape is quiet (RV <= median) and not a bear trend
        is_quiet = rv_ratio <= 1.05
        is_bull_or_neutral = trend_regime in ("bull", "neutral")
        is_safe_regime = market_regime not in ("CRISIS", "MELTDOWN")

        if is_quiet and is_bull_or_neutral and is_safe_regime:
            confidence = 0.70
            if trend_regime == "bull":
                confidence += 0.15
            if rv_ratio < 0.85:
                confidence += 0.10
            confidence = min(0.95, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_regime_quiet_tape_entry",
                    "current_rv": round(current_rv, 6),
                    "median_rv": round(median_rv, 6),
                    "rv_ratio": round(rv_ratio, 3),
                    "trend_regime": trend_regime,
                    "market_regime": market_regime,
                    "price": current_close,
                }
            )

        return None