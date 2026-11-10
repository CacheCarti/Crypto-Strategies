from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBReversion(Strategy):
    METADATA = {
        "name": "SolPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _bollinger_pct_b(self, closes):
        if len(closes) < self.period:
            return None, None
        slice_c = closes[-self.period:]
        mean = sum(slice_c) / self.period
        variance = sum((x - mean) ** 2 for x in slice_c) / self.period
        std = math.sqrt(variance)
        if std == 0.0:
            return None, None
        upper = mean + self.num_std * std
        lower = mean - self.num_std * std
        width = upper - lower
        if width == 0.0:
            return None, None
        pct_b = (closes[-1] - lower) / width
        return pct_b, mean

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 1:
            return None

        # Filter out extreme crisis regimes to prevent aggressive fade during sustained liquidation
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": crisis_score}
                )
            return None

        curr_pct_b, curr_mid = self._bollinger_pct_b(closes)
        prev_pct_b, _ = self._bollinger_pct_b(closes[:-1])

        if curr_pct_b is None or prev_pct_b is None or curr_mid is None:
            return None

        curr_close = closes[-1]
        has_pos = ctx.has_position()
        direction = ctx.position_direction()

        # Exit logic: reversion to mid-band or counter-reversal
        if has_pos:
            if direction == "long" and curr_close >= curr_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "midband_reached_long",
                        "pct_b": round(curr_pct_b, 3),
                        "close": round(curr_close, 3),
                        "mid": round(curr_mid, 3),
                    }
                )
            elif direction == "short" and curr_close <= curr_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "midband_reached_short",
                        "pct_b": round(curr_pct_b, 3),
                        "close": round(curr_close, 3),
                        "mid": round(curr_mid, 3),
                    }
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Failed break condition: prior bar pierced boundary (< 0 or > 1), current bar snaps back inside
        long_reclaim = prev_pct_b < 0.0 and curr_pct_b >= 0.05 and curr_pct_b <= 0.40
        short_reclaim = prev_pct_b > 1.0 and curr_pct_b <= 0.95 and curr_pct_b >= 0.60

        if long_reclaim:
            conf = min(0.85, 0.55 + max(0.0, -prev_pct_b) * 0.3)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pct_b_failed_break_long",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "close": round(curr_close, 3),
                    "mid": round(curr_mid, 3),
                }
            )

        if short_reclaim:
            conf = min(0.85, 0.55 + max(0.0, prev_pct_b - 1.0) * 0.3)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pct_b_failed_break_short",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "close": round(curr_close, 3),
                    "mid": round(curr_mid, 3),
                }
            )

        return None