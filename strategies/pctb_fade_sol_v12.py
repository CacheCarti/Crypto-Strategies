from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.last_exit_bar = -100
        self.cooldown_bars = 4

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
        closes = ctx.closes(self.period + 3)
        lows = ctx.lows(3)
        highs = ctx.highs(3)

        if len(closes) < self.period + 2:
            return None

        # Calculate current Bollinger Bands
        mid, upper, lower = self._bollinger(closes, self.period, self.num_std)
        if mid is None or upper is None or lower is None or upper == lower:
            return None

        # Position management: Target mean-reversion to the mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            curr_close = closes[-1]
            if pos_dir == "long" and curr_close >= mid:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "mid_band_target_reached_long",
                    "close": curr_close,
                    "mid": mid
                })
            elif pos_dir == "short" and curr_close <= mid:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "mid_band_target_reached_short",
                    "close": curr_close,
                    "mid": mid
                })
            return None

        # Cooldown guard after exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check market stress filter to avoid catching knives during crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Calculate %B for previous and current bars
        prev_mid, prev_upper, prev_lower = self._bollinger(closes[:-1], self.period, self.num_std)
        if prev_upper is None or prev_lower is None or prev_upper == prev_lower:
            return None

        prev_pct_b = (closes[-2] - prev_lower) / (prev_upper - prev_lower)
        curr_pct_b = (closes[-1] - lower) / (upper - lower)

        # Long trigger: previous bar probed/closed below lower band, current bar closes back inside
        # Ensure bandwidth is healthy (not flat consolidation)
        bw_pct = (upper - lower) / mid
        if bw_pct < 0.015:
            return None

        # Bullish rejection: previous bar pierced lower band and current bar crossed back above 0.05
        if (prev_pct_b < 0.0 or lows[-2] < prev_lower) and prev_pct_b < 0.10 and curr_pct_b > 0.08 and curr_pct_b < 0.45:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_pierce_reclaim",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "mid": round(mid, 2),
                    "bw_pct": round(bw_pct, 4)
                }
            )

        # Bearish rejection: previous bar pierced upper band and current bar crossed back below 0.95
        if (prev_pct_b > 1.0 or highs[-2] > prev_upper) and prev_pct_b > 0.90 and curr_pct_b < 0.92 and curr_pct_b > 0.55:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_pierce_reclaim",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "mid": round(mid, 2),
                    "bw_pct": round(bw_pct, 4)
                }
            )

        return None