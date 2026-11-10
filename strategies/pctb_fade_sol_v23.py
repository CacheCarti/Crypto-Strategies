from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger(self, closes, period=20, num_std=2.0):
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid churn and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        mid, upper, lower = self._bollinger(closes, self.bb_period, self.bb_std)
        if mid is None or upper == lower:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        
        # Cross-asset trend/shock filter to avoid fading strong runaway momentum
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        band_width = upper - lower
        pct_b_curr = (curr_close - lower) / band_width
        pct_b_prev = (prev_close - lower) / band_width

        # Position exit logic: mean reversion back to mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close >= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "mid_band_reached_exit_long",
                    "pct_b": pct_b_curr,
                    "close": curr_close,
                    "mid": mid
                })
            elif pos_dir == "short" and curr_close <= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "mid_band_reached_exit_short",
                    "pct_b": pct_b_curr,
                    "close": curr_close,
                    "mid": mid
                })
            return None

        # Long Entry: previous bar closed below band (%B < 0), current bar closes back inside (%B >= 0.05)
        # Cross-asset filter: BTC not in severe dump (btc_ret >= -3.5%) to prevent catching falling knives
        if pct_b_prev < 0.0 and 0.05 <= pct_b_curr <= 0.40 and btc_ret > -3.5:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pctb_lower_rejection_long",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "close": curr_close,
                    "btc_return_pct": btc_ret
                }
            )

        # Short Entry: previous bar closed above band (%B > 1), current bar closes back inside (%B <= 0.95)
        # Cross-asset filter: BTC not in explosive breakout (btc_ret <= 3.5%)
        if pct_b_prev > 1.0 and 0.60 <= pct_b_curr <= 0.95 and btc_ret < 3.5:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pctb_upper_rejection_short",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "close": curr_close,
                    "btc_return_pct": btc_ret
                }
            )

        return None