from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeEventStrategy(Strategy):
    METADATA = {
        "name": "SolPctBFadeEventStrategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.num_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _bollinger_bands(self, closes):
        if len(closes) < self.bb_period:
            return None, None, None
        slice_c = closes[-self.bb_period:]
        mean = sum(slice_c) / self.bb_period
        variance = sum((x - mean) ** 2 for x in slice_c) / self.bb_period
        std = math.sqrt(variance)
        upper = mean + self.num_std * std
        lower = mean - self.num_std * std
        return lower, mean, upper

    def _calc_pct_b(self, close, lower, upper):
        bandwidth = upper - lower
        if bandwidth <= 0:
            return 0.5
        return (close - lower) / bandwidth

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Calculate current Bollinger Bands & %B
        lower_curr, mid_curr, upper_curr = self._bollinger_bands(closes)
        if lower_curr is None or mid_curr is None or upper_curr is None:
            return None
        pct_b_curr = self._calc_pct_b(closes[-1], lower_curr, upper_curr)

        # Calculate previous bar's Bollinger Bands & %B
        prev_closes = closes[:-1]
        lower_prev, mid_prev, upper_prev = self._bollinger_bands(prev_closes)
        if lower_prev is None or upper_prev is None:
            return None
        pct_b_prev = self._calc_pct_b(prev_closes[-1], lower_prev, upper_prev)

        # Position management: Take profit / exit at mid-band (mean reversion completed)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and closes[-1] >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_tp_mid_band_reached",
                        "close": closes[-1],
                        "mid": mid_curr,
                        "pct_b": pct_b_curr,
                    },
                )
            elif direction == "short" and closes[-1] <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_tp_mid_band_reached",
                        "close": closes[-1],
                        "mid": mid_curr,
                        "pct_b": pct_b_curr,
                    },
                )
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter: skip entering during high crisis / meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Event definitions: Failed breakout reverting back inside bands
        # Long: previous bar closed below lower band (%B < 0.0), current closes back inside (%B in [0.05, 0.45])
        is_long_event = (pct_b_prev < 0.0) and (0.05 <= pct_b_curr <= 0.45)
        # Short: previous bar closed above upper band (%B > 1.0), current closes back inside (%B in [0.55, 0.95])
        is_short_event = (pct_b_prev > 1.0) and (0.55 <= pct_b_curr <= 0.95)

        if is_long_event:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=320.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "failed_breakdown_pct_b_reversion",
                    "pct_b_prev": round(pct_b_prev, 4),
                    "pct_b_curr": round(pct_b_curr, 4),
                    "lower_band": round(lower_curr, 2),
                    "mid_band": round(mid_curr, 2),
                    "close": round(closes[-1], 2),
                },
            )

        if is_short_event:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=320.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "failed_breakout_pct_b_reversion",
                    "pct_b_prev": round(pct_b_prev, 4),
                    "pct_b_curr": round(pct_b_curr, 4),
                    "upper_band": round(upper_curr, 2),
                    "mid_band": round(mid_curr, 2),
                    "close": round(closes[-1], 2),
                },
            )

        return None