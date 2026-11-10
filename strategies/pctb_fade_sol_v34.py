from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger(self, closes: list, period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Prior bar Bollinger Bands
        prior_closes = closes[:-1]
        mid_prev, upper_prev, lower_prev = self._bollinger(prior_closes, self.period, self.num_std)
        # Current bar Bollinger Bands
        mid_curr, upper_curr, lower_curr = self._bollinger(closes, self.period, self.num_std)

        if mid_prev is None or mid_curr is None:
            return None

        prev_band_width = upper_prev - lower_prev
        curr_band_width = upper_curr - lower_curr
        if prev_band_width <= 0 or curr_band_width <= 0:
            return None

        prev_close = closes[-2]
        curr_close = closes[-1]

        pct_b_prev = (prev_close - lower_prev) / prev_band_width
        pct_b_curr = (curr_close - lower_curr) / curr_band_width

        # Position management: exit on mid-band cross
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close >= mid_curr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "pctb_fade_mid_exit_long",
                    "close": curr_close,
                    "mid": mid_curr,
                    "pct_b": pct_b_curr
                })
            elif pos_dir == "short" and curr_close <= mid_curr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "pctb_fade_mid_exit_short",
                    "close": curr_close,
                    "mid": mid_curr,
                    "pct_b": pct_b_curr
                })
            return None

        # Cooldown guard to avoid churn and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime filter: avoid taking mean-reversion trades in crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility expansion check: avoid trading during ultra-narrow consolidation squeeze
        # Band width should be at least 1.2% (120 bps) of mid-price
        min_width_pct = 0.012
        if (curr_band_width / mid_curr) < min_width_pct:
            return None

        # Rejection/failed-breakout conditions:
        # Long: previous bar closed cleanly below lower band (%B < -0.05), current bar recaptures (%B > 0.05 and < 0.40)
        long_condition = (pct_b_prev < -0.05) and (0.05 <= pct_b_curr <= 0.40)

        # Short: previous bar closed cleanly above upper band (%B > 1.05), current bar recaptures (%B < 0.95 and > 0.60)
        short_condition = (pct_b_prev > 1.05) and (0.60 <= pct_b_curr <= 0.95)

        if long_condition:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=450.0,
                metadata={
                    "reason": "pctb_fade_long_reclaim",
                    "pct_b_prev": round(pct_b_prev, 4),
                    "pct_b_curr": round(pct_b_curr, 4),
                    "close": curr_close,
                    "lower_band": lower_curr
                }
            )

        if short_condition:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=350.0,
                take_profit_bps=450.0,
                metadata={
                    "reason": "pctb_fade_short_reclaim",
                    "pct_b_prev": round(pct_b_prev, 4),
                    "pct_b_curr": round(pct_b_curr, 4),
                    "close": curr_close,
                    "upper_band": upper_curr
                }
            )

        return None