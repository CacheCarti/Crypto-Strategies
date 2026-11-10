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
        self.bb_std = 2.0
        self.cooldown_bars = 4
        self.last_trade_bar = -999

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Compute current and previous Bollinger Bands
        curr_bb = self._bollinger(closes[-self.bb_period:], self.bb_period, self.bb_std)
        prev_bb = self._bollinger(closes[-self.bb_period - 1:-1], self.bb_period, self.bb_std)

        if not curr_bb or not prev_bb:
            return None

        mid, upper, lower = curr_bb
        prev_mid, prev_upper, prev_lower = prev_bb

        bandwidth = upper - lower
        prev_bandwidth = prev_upper - prev_lower
        if bandwidth <= 0 or prev_bandwidth <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        pct_b = (curr_close - lower) / bandwidth
        prev_pct_b = (prev_close - prev_lower) / prev_bandwidth

        # Active position management: exit when reverting to mid-band
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_reverted_to_mid_band",
                    "pct_b": pct_b,
                    "close": curr_close,
                    "mid": mid
                })
            elif direction == "short" and curr_close <= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_reverted_to_mid_band",
                    "pct_b": pct_b,
                    "close": curr_close,
                    "mid": mid
                })
            return None

        # Post-trade cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime gate to address prior failure (oos_reject from trending/crisis chop):
        # Avoid fading band breaks during extreme crisis or runaway high-volatility expansions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long: previous bar broke below lower band (pct_b < 0.0), current bar closes back inside
        if prev_pct_b < 0.0 and pct_b >= 0.05 and pct_b < 0.45:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_rejection_long",
                    "prev_pct_b": round(prev_pct_b, 4),
                    "curr_pct_b": round(pct_b, 4),
                    "lower_band": round(lower, 2),
                    "mid_band": round(mid, 2),
                    "close": round(curr_close, 2)
                }
            )

        # Short: previous bar broke above upper band (pct_b > 1.0), current bar closes back inside
        if prev_pct_b > 1.0 and pct_b <= 0.95 and pct_b > 0.55:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_rejection_short",
                    "prev_pct_b": round(prev_pct_b, 4),
                    "curr_pct_b": round(pct_b, 4),
                    "upper_band": round(upper, 2),
                    "mid_band": round(mid, 2),
                    "close": round(curr_close, 2)
                }
            )

        return None