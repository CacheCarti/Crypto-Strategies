from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_mult = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.bb_period + 3)
        if len(closes) < self.bb_period + 2:
            return None

        # Calculate current Bollinger Bands
        bb_curr = self._bollinger(closes, self.bb_period, self.bb_mult)
        # Calculate previous bar's Bollinger Bands using slice without last bar
        bb_prev = self._bollinger(closes[:-1], self.bb_period, self.bb_mult)

        if not bb_curr or not bb_prev:
            return None

        mid_curr, upper_curr, lower_curr = bb_curr
        _, upper_prev, lower_prev = bb_prev

        band_width_curr = upper_curr - lower_curr
        band_width_prev = upper_prev - lower_prev

        if band_width_curr <= 0 or band_width_prev <= 0:
            return None

        # %B calculation: < 0 means below lower band, > 1 means above upper band
        pct_b_curr = (closes[-1] - lower_curr) / band_width_curr
        pct_b_prev = (closes[-2] - lower_prev) / band_width_prev

        # Active position management: exit cleanly when reverting across mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and closes[-1] >= mid_curr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_tp_mid_band_reversion",
                    "close": closes[-1],
                    "mid": mid_curr,
                    "pct_b": pct_b_curr
                })
            elif pos_dir == "short" and closes[-1] <= mid_curr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_tp_mid_band_reversion",
                    "close": closes[-1],
                    "mid": mid_curr,
                    "pct_b": pct_b_curr
                })
            return None

        # Mandatory cooldown check after prior trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety: avoid entering fades during full market meltdown/crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Strict event boundary:
        # Long: Previous bar breached outside lower band (%B < 0), current bar closes back inside with green candle
        long_trigger = (pct_b_prev < 0.0) and (pct_b_curr >= 0.05) and (closes[-1] > ctx.bar.open)
        
        # Short: Previous bar breached outside upper band (%B > 1), current bar closes back inside with red candle
        short_trigger = (pct_b_prev > 1.0) and (pct_b_curr <= 0.95) and (closes[-1] < ctx.bar.open)

        if long_trigger:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pctb_lower_rejection_reentry",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "lower_band": lower_curr,
                    "close": closes[-1],
                    "mid_band": mid_curr
                }
            )

        if short_trigger:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pctb_upper_rejection_reentry",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "upper_band": upper_curr,
                    "close": closes[-1],
                    "mid_band": mid_curr
                }
            )

        return None