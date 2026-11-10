from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

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
        closes = ctx.closes(self.period + 3)
        if len(closes) < self.period + 2:
            return None

        # Compute current bands
        bands_curr = self._bollinger(closes, self.period, self.num_std)
        # Compute previous bar bands
        bands_prev = self._bollinger(closes[:-1], self.period, self.num_std)

        if not bands_curr or not bands_prev:
            return None

        mid_curr, upper_curr, lower_curr = bands_curr
        mid_prev, upper_prev, lower_prev = bands_prev

        band_width_curr = upper_curr - lower_curr
        band_width_prev = upper_prev - lower_prev

        if band_width_curr <= 0 or band_width_prev <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        pct_b_curr = (curr_close - lower_curr) / band_width_curr
        pct_b_prev = (prev_close - lower_prev) / band_width_prev

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_mid_band_target_reached", "pct_b": pct_b_curr, "mid": mid_curr, "close": curr_close}
                )
            elif direction == "short" and curr_close <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_mid_band_target_reached", "pct_b": pct_b_curr, "mid": mid_curr, "close": curr_close}
                )
            return None

        # Cooldown guard to avoid churn and bleeding on friction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety: avoid crisis/meltdown flushes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: failed breakout re-entering the band
        # Long: previous bar closed below lower band (%B < 0), current bar closed back inside (%B in [0.05, 0.45])
        long_reentry = (pct_b_prev < 0.0) and (0.05 <= pct_b_curr <= 0.45)
        # Short: previous bar closed above upper band (%B > 1.0), current bar closed back inside (%B in [0.55, 0.95])
        short_reentry = (pct_b_prev > 1.0) and (0.55 <= pct_b_curr <= 0.95)

        if long_reentry:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_oversold_failed_breakdown_long",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "lower_band": lower_curr,
                    "close": curr_close,
                }
            )

        if short_reentry:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_overbought_failed_breakout_short",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "upper_band": upper_curr,
                    "close": curr_close,
                }
            )

        return None