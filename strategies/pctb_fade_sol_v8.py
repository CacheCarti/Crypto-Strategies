from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolBollingerPctBReversion(Strategy):
    METADATA = {
        "name": "SolBollingerPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.35
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _calc_bollinger(self, closes: list, period: int, num_std: float):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        upper = mean + num_std * std
        lower = mean - num_std * std
        return mean, upper, lower

    def _calc_pct_b(self, close: float, upper: float, lower: float) -> float:
        band_width = upper - lower
        if band_width <= 0:
            return 0.5
        return (close - lower) / band_width

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + self.rsi_period + 5)
        if len(closes) < self.period + self.rsi_period:
            return None

        # Filter crisis conditions to protect against runaway trends
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.60 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate current Bollinger Bands & %B
        mean_curr, upper_curr, lower_curr = self._calc_bollinger(closes, self.period, self.num_std)
        if mean_curr is None:
            return None
        pct_b_curr = self._calc_pct_b(closes[-1], upper_curr, lower_curr)

        # Active position management: exit cleanly at mid-band reversion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and pct_b_curr >= 0.50:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_hit_mid_band_reversion",
                        "pct_b": round(pct_b_curr, 4),
                        "mid_band": round(mean_curr, 2),
                        "close": round(ctx.bar.close, 2),
                    },
                )
            elif pos_dir == "short" and pct_b_curr <= 0.50:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_hit_mid_band_reversion",
                        "pct_b": round(pct_b_curr, 4),
                        "mid_band": round(mean_curr, 2),
                        "close": round(ctx.bar.close, 2),
                    },
                )
            return None

        # Hard cooldown guard to prevent overtrading and fee drain
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Previous bar Bollinger Bands & %B
        prev_closes = closes[:-1]
        mean_prev, upper_prev, lower_prev = self._calc_bollinger(prev_closes, self.period, self.num_std)
        if mean_prev is None:
            return None
        pct_b_prev = self._calc_pct_b(prev_closes[-1], upper_prev, lower_prev)

        # Complement with RSI to confirm true exhaustion
        rsi = self._calc_rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # High-Conviction Long: Significant prior puncture below 2.35 std band, re-entry inside band + oversold RSI
        if pct_b_prev < -0.05 and 0.02 <= pct_b_curr <= 0.35 and rsi <= 34.0:
            penetration_depth = max(0.0, -pct_b_prev)
            confidence = min(0.85, 0.65 + penetration_depth * 0.25)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "deep_lower_pierce_reentry_oversold",
                    "pct_b_prev": round(pct_b_prev, 4),
                    "pct_b_curr": round(pct_b_curr, 4),
                    "rsi": round(rsi, 2),
                    "lower_band": round(lower_curr, 2),
                    "mid_band": round(mean_curr, 2),
                    "close": round(ctx.bar.close, 2),
                },
            )

        # High-Conviction Short: Significant prior puncture above 2.35 std band, re-entry inside band + overbought RSI
        if pct_b_prev > 1.05 and 0.65 <= pct_b_curr <= 0.98 and rsi >= 66.0:
            penetration_depth = max(0.0, pct_b_prev - 1.0)
            confidence = min(0.85, 0.65 + penetration_depth * 0.25)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "deep_upper_pierce_reentry_overbought",
                    "pct_b_prev": round(pct_b_prev, 4),
                    "pct_b_curr": round(pct_b_curr, 4),
                    "rsi": round(rsi, 2),
                    "upper_band": round(upper_curr, 2),
                    "mid_band": round(mean_curr, 2),
                    "close": round(ctx.bar.close, 2),
                },
            )

        return None