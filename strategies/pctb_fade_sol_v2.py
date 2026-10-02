from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPercentBReversion(Strategy):
    METADATA = {
        "name": "SOL Percent B Mean Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 24
        self.bb_std = 2.35
        self.rsi_period = 14
        self.cooldown_bars = 14
        self.last_exit_bar = -999

    def _calc_bollinger_and_pct_b(self, closes: list, period: int, std_mult: float):
        if len(closes) < period:
            return None, None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        upper = mean + std_mult * std
        lower = mean - std_mult * std
        band_width = upper - lower
        if band_width <= 0:
            return mean, upper, lower, 0.5
        pct_b = (closes[-1] - lower) / band_width
        return mean, upper, lower, pct_b

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        needed_bars = max(self.bb_period, self.rsi_period) + 3
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        # Calculate metrics
        mid, upper, lower, pct_b = self._calc_bollinger_and_pct_b(closes, self.bb_period, self.bb_std)
        prev_mid, prev_upper, prev_lower, prev_pct_b = self._calc_bollinger_and_pct_b(closes[:-1], self.bb_period, self.bb_std)
        rsi = self._rsi(closes, self.rsi_period)

        if mid is None or prev_mid is None or rsi is None:
            return None

        current_price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS", "HIGH_VOL"):
            return None

        # Position Management: Exit at mid-band mean reversion target
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_price >= mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "long_tp_mid_band_reverted",
                    "price": current_price,
                    "mid_band": round(mid, 2),
                    "pct_b": round(pct_b, 4),
                    "rsi": round(rsi, 2)
                })
            elif pos_dir == "short" and current_price <= mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "short_tp_mid_band_reverted",
                    "price": current_price,
                    "mid_band": round(mid, 2),
                    "pct_b": round(pct_b, 4),
                    "rsi": round(rsi, 2)
                })
            return None

        # Mandatory Cooldown Gate to limit trade count and avoid friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # High conviction filter: Penetration outside 2.35-std band followed by firm reclaim + RSI confluence
        # Long Setup: Previous bar clearly below lower band (%B <= -0.02), current bar closes firmly inside (%B >= 0.08) and RSI oversold
        if prev_pct_b <= -0.02 and pct_b >= 0.08 and rsi <= 38.0:
            confidence = min(0.85, 0.65 + abs(prev_pct_b) * 0.2)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_lower_band_reclaim_oversold",
                    "prev_pct_b": round(prev_pct_b, 4),
                    "pct_b": round(pct_b, 4),
                    "rsi": round(rsi, 2),
                    "lower_band": round(lower, 2),
                    "mid_band": round(mid, 2),
                    "price": current_price
                }
            )

        # Short Setup: Previous bar clearly above upper band (%B >= 1.02), current bar closes firmly inside (%B <= 0.92) and RSI overbought
        if prev_pct_b >= 1.02 and pct_b <= 0.92 and rsi >= 62.0:
            confidence = min(0.85, 0.65 + (prev_pct_b - 1.0) * 0.2)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_upper_band_rejection_overbought",
                    "prev_pct_b": round(prev_pct_b, 4),
                    "pct_b": round(pct_b, 4),
                    "rsi": round(rsi, 2),
                    "upper_band": round(upper, 2),
                    "mid_band": round(mid, 2),
                    "price": current_price
                }
            )

        return None