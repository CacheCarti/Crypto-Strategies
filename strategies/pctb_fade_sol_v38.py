from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PctBFadeSol(Strategy):
    METADATA = {
        "name": "PctBFadeSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

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

        # Check regime filter to prevent counter-trend fades in melt-down markets
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Current and previous Bollinger Bands
        curr_mid, curr_upper, curr_lower = self._bollinger(closes[-self.period:], self.period, self.num_std)
        prev_closes = closes[-(self.period + 1):-1]
        prev_mid, prev_upper, prev_lower = self._bollinger(prev_closes, self.period, self.num_std)

        if curr_mid is None or prev_mid is None:
            return None

        curr_bandwidth = curr_upper - curr_lower
        prev_bandwidth = prev_upper - prev_lower
        if curr_bandwidth <= 0 or prev_bandwidth <= 0:
            return None

        c_now = closes[-1]
        c_prev = closes[-2]

        pctb_now = (c_now - curr_lower) / curr_bandwidth
        pctb_prev = (c_prev - prev_lower) / prev_bandwidth

        # Manage existing position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and c_now >= curr_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "mid_band_target_hit_long",
                    "pctb": round(pctb_now, 4),
                    "close": round(c_now, 2),
                    "mid": round(curr_mid, 2)
                })
            elif pos_dir == "short" and c_now <= curr_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "mid_band_target_hit_short",
                    "pctb": round(pctb_now, 4),
                    "close": round(c_now, 2),
                    "mid": round(curr_mid, 2)
                })
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Reversion entries: failed band breaks (re-entering the bands)
        # Long: previous close below lower band (pctb < 0.0), current close back inside (pctb >= 0.0)
        # To avoid false recoveries during severe breakdowns, ensure recovery is firm but below median
        if pctb_prev < 0.0 and 0.0 <= pctb_now <= 0.40:
            confidence = min(0.85, 0.55 + max(0.0, (0.40 - pctb_now) * 0.5))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=280.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "pctb_reentry_oversold_fade",
                    "pctb_prev": round(pctb_prev, 4),
                    "pctb_now": round(pctb_now, 4),
                    "close": round(c_now, 2),
                    "lower": round(curr_lower, 2),
                    "mid": round(curr_mid, 2)
                }
            )

        # Short: previous close above upper band (pctb > 1.0), current close back inside (pctb <= 1.0)
        if pctb_prev > 1.0 and 0.60 <= pctb_now <= 1.0:
            confidence = min(0.85, 0.55 + max(0.0, (pctb_now - 0.60) * 0.5))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=280.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "pctb_reentry_overbought_fade",
                    "pctb_prev": round(pctb_prev, 4),
                    "pctb_now": round(pctb_now, 4),
                    "close": round(c_now, 2),
                    "upper": round(curr_upper, 2),
                    "mid": round(curr_mid, 2)
                }
            )

        return None