from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeEventReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeEventReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _bollinger_bands(self, closes):
        if len(closes) < self.period:
            return None, None, None
        slice_c = closes[-self.period:]
        mean = sum(slice_c) / self.period
        variance = sum((x - mean) ** 2 for x in slice_c) / self.period
        std = math.sqrt(variance)
        return mean, mean + self.num_std * std, mean - self.num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 3)
        lows = ctx.lows(self.period + 3)
        highs = ctx.highs(self.period + 3)

        if len(closes) < self.period + 2:
            return None

        # Cooldown guard after trade exits or rapid re-entries
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Prior bar Bollinger Bands
        prior_closes = closes[:-1]
        mid_prev, upper_prev, lower_prev = self._bollinger_bands(prior_closes)
        # Current bar Bollinger Bands
        mid_curr, upper_curr, lower_curr = self._bollinger_bands(closes)

        if None in (mid_prev, upper_prev, lower_prev, mid_curr, upper_curr, lower_curr):
            return None

        band_width_prev = upper_prev - lower_prev
        band_width_curr = upper_curr - lower_curr

        if band_width_prev <= 0 or band_width_curr <= 0:
            return None

        close_prev = closes[-2]
        close_curr = closes[-1]
        low_prev = lows[-2]
        high_prev = highs[-2]

        pct_b_prev = (close_prev - lower_prev) / band_width_prev
        pct_b_curr = (close_curr - lower_curr) / band_width_curr

        # Manage open position exits via mid-band touch or target
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and close_curr >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_tp_mid_band_reversion",
                        "close": close_curr,
                        "mid": mid_curr,
                        "pct_b": pct_b_curr
                    }
                )
            elif pos_dir == "short" and close_curr <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_tp_mid_band_reversion",
                        "close": close_curr,
                        "mid": mid_curr,
                        "pct_b": pct_b_curr
                    }
                )
            return None

        # Require minimum spacing between entries
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # Event: Bear-trap / Failed downward band break
        # Prior bar was below or poked beneath lower band, current bar closes firmly back inside
        long_event = (pct_b_prev < 0.0 or low_prev < lower_prev) and (pct_b_curr > 0.08 and pct_b_curr < 0.45)
        # Event: Bull-trap / Failed upward band break
        # Prior bar was above or poked above upper band, current bar closes firmly back inside
        short_event = (pct_b_prev > 1.0 or high_prev > upper_prev) and (pct_b_curr < 0.92 and pct_b_curr > 0.55)

        if long_event:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_lower_band_break_reversion",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "close": close_curr,
                    "lower_band": lower_curr,
                    "mid_band": mid_curr
                }
            )

        if short_event:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_upper_band_break_reversion",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "close": close_curr,
                    "upper_band": upper_curr,
                    "mid_band": mid_curr
                }
            )

        return None