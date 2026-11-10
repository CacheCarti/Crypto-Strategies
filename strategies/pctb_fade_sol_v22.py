from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 320.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _bollinger_b(self, closes):
        if len(closes) < self.bb_period:
            return None, None, None, None
        slice_c = closes[-self.bb_period:]
        mean = sum(slice_c) / self.bb_period
        variance = sum((x - mean) ** 2 for x in slice_c) / self.bb_period
        std = math.sqrt(variance)
        if std == 0.0:
            return None, None, None, None
        upper = mean + self.num_std * std
        lower = mean - self.num_std * std
        band_width = upper - lower
        if band_width == 0.0:
            return None, None, None, None
        pct_b = (closes[-1] - lower) / band_width
        return pct_b, mean, upper, lower

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Filter crisis or meltdown regimes to avoid catching falling knives
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": regime}
                )
            return None

        # Compute current %B and previous %B
        pct_b_curr, mid_curr, upper_curr, lower_curr = self._bollinger_b(closes)
        pct_b_prev, _, _, _ = self._bollinger_b(closes[:-1])

        if pct_b_curr is None or pct_b_prev is None:
            return None

        pos_dir = ctx.position_direction()

        # Dynamic profit taking / mean reversion exit at mid-band
        if pos_dir == "long":
            if closes[-1] >= mid_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_long_mid_band_reached",
                        "pct_b": round(pct_b_curr, 3),
                        "close": round(closes[-1], 2),
                        "mid": round(mid_curr, 2)
                    }
                )
            return None

        if pos_dir == "short":
            if closes[-1] <= mid_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_short_mid_band_reached",
                        "pct_b": round(pct_b_curr, 3),
                        "close": round(closes[-1], 2),
                        "mid": round(mid_curr, 2)
                    }
                )
            return None

        # Enforce strict cooldown between exits and new entries to suppress overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Re-entry conditions: Failed breakdown/breakout closing back inside the bands
        # Long: previous bar was strictly below lower band (%B < 0), current bar reclaimed inside (%B >= 0.05 and < 0.45)
        if pct_b_prev < 0.0 and 0.05 <= pct_b_curr <= 0.45:
            confidence = min(0.85, max(0.55, 0.55 + (0.45 - pct_b_curr)))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_failed_breakdown_reclaimed",
                    "pct_b_prev": round(pct_b_prev, 3),
                    "pct_b_curr": round(pct_b_curr, 3),
                    "lower_band": round(lower_curr, 2),
                    "close": round(closes[-1], 2)
                }
            )

        # Short: previous bar was strictly above upper band (%B > 1.0), current bar closed back inside (%B <= 0.95 and > 0.55)
        if pct_b_prev > 1.0 and 0.55 <= pct_b_curr <= 0.95:
            confidence = min(0.85, max(0.55, 0.55 + (pct_b_curr - 0.55)))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_failed_breakout_reclaimed",
                    "pct_b_prev": round(pct_b_prev, 3),
                    "pct_b_curr": round(pct_b_curr, 3),
                    "upper_band": round(upper_curr, 2),
                    "close": round(closes[-1], 2)
                }
            )

        return None