from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        upper = mean + num_std * std
        lower = mean - num_std * std
        return mean, upper, lower

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 3)
        if len(closes) < self.period + 2:
            return None

        # Check regime to avoid fading strong meltdowns/crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Current and previous bar bands
        curr_bb = self._bollinger(closes[:-1] + [closes[-1]], self.period, self.num_std)
        prev_bb = self._bollinger(closes[:-1], self.period, self.num_std)
        if not curr_bb or not prev_bb:
            return None

        mid, upper, lower = curr_bb
        prev_mid, prev_upper, prev_lower = prev_bb

        curr_close = closes[-1]
        prev_close = closes[-2]

        width = upper - lower
        if width <= 0:
            return None

        pct_b_curr = (curr_close - lower) / width
        prev_width = prev_upper - prev_lower
        pct_b_prev = (prev_close - prev_lower) / prev_width if prev_width > 0 else 0.5

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion target: Mid-band reached
            if pos_dir == "long" and curr_close >= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_tp_mid_band_touch",
                    "close": curr_close,
                    "mid": mid,
                    "pct_b": pct_b_curr
                })
            elif pos_dir == "short" and curr_close <= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_tp_mid_band_touch",
                    "close": curr_close,
                    "mid": mid,
                    "pct_b": pct_b_curr
                })
            return None

        # Entry logic: strict event boundary crossing
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Re-entry back inside bands after a clean pierce:
        # Long: previous close was distinctly below lower band (pct_b < 0.0), current closed back inside (0.05 <= pct_b <= 0.40)
        if pct_b_prev < 0.0 and 0.05 <= pct_b_curr <= 0.40:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_pierce_reclaim",
                    "pct_b_curr": pct_b_curr,
                    "pct_b_prev": pct_b_prev,
                    "close": curr_close,
                    "lower": lower,
                    "mid": mid
                }
            )

        # Short: previous close was distinctly above upper band (pct_b > 1.0), current closed back inside (0.60 <= pct_b <= 0.95)
        if pct_b_prev > 1.0 and 0.60 <= pct_b_curr <= 0.95:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_pierce_reclaim",
                    "pct_b_curr": pct_b_curr,
                    "pct_b_prev": pct_b_prev,
                    "close": curr_close,
                    "upper": upper,
                    "mid": mid
                }
            )

        return None