from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctbFade(Strategy):
    METADATA = {
        "name": "SolPctbFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _bollinger_bands(self, closes):
        if len(closes) < self.period:
            return None, None, None
        window = closes[-self.period:]
        sma = sum(window) / self.period
        variance = sum((x - sma) ** 2 for x in window) / self.period
        std = math.sqrt(variance)
        return sma, sma + self.num_std * std, sma - self.num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 3)
        if len(closes) < self.period + 2:
            return None

        # Compute bands for previous bar to evaluate breach
        prev_closes = closes[:-1]
        mid_prev, upper_prev, lower_prev = self._bollinger_bands(prev_closes)
        
        # Compute current bands
        mid_curr, upper_curr, lower_curr = self._bollinger_bands(closes)

        if None in (mid_prev, upper_prev, lower_prev, mid_curr, upper_curr, lower_curr):
            return None

        prev_c = closes[-2]
        curr_c = closes[-1]

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_c >= mid_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_midband_reached", "close": curr_c, "mid": mid_curr}
                )
            elif pos_dir == "short" and curr_c <= mid_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_midband_reached", "close": curr_c, "mid": mid_curr}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown states to prevent fighting relentless liquidations
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # %B Reversion logic:
        # Long entry: Previous bar breached below lower band; current bar closes back inside
        band_width_prev = upper_prev - lower_prev
        band_width_curr = upper_curr - lower_curr
        if band_width_prev <= 0 or band_width_curr <= 0:
            return None

        pctb_prev = (prev_c - lower_prev) / band_width_prev
        pctb_curr = (curr_c - lower_curr) / band_width_curr

        # Failed lower band breakdown -> Mean-reversion Long
        if pctb_prev < 0.0 and pctb_curr >= 0.05 and curr_c < mid_curr:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=300.0,
                take_profit_bps=420.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "pctb_lower_rejection_long",
                    "pctb_prev": round(pctb_prev, 3),
                    "pctb_curr": round(pctb_curr, 3),
                    "close": curr_c,
                    "lower_band": lower_curr,
                }
            )

        # Failed upper band breakout -> Mean-reversion Short
        if pctb_prev > 1.0 and pctb_curr <= 0.95 and curr_c > mid_curr:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=300.0,
                take_profit_bps=420.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "pctb_upper_rejection_short",
                    "pctb_prev": round(pctb_prev, 3),
                    "pctb_curr": round(pctb_curr, 3),
                    "close": curr_c,
                    "upper_band": upper_curr,
                }
            )

        return None