from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeEventReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeEventReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 5
        self.last_exit_bar = -999

    def _bollinger(self, closes):
        if len(closes) < self.period:
            return None
        sub = closes[-self.period:]
        mean = sum(sub) / self.period
        variance = sum((x - mean) ** 2 for x in sub) / self.period
        std = math.sqrt(variance)
        return mean, mean + self.num_std * std, mean - self.num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 3)
        if len(closes) < self.period + 2:
            return None

        # Compute Bollinger Bands for current and prior bar
        bb_curr = self._bollinger(closes)
        bb_prev = self._bollinger(closes[:-1])
        if not bb_curr or not bb_prev:
            return None

        mid, upper, lower = bb_curr
        prev_mid, prev_upper, prev_lower = bb_prev

        band_width = upper - lower
        if band_width <= 0:
            return None

        prev_band_width = prev_upper - prev_lower
        if prev_band_width <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        pct_b = (curr_close - lower) / band_width
        prev_pct_b = (prev_close - prev_lower) / prev_band_width

        # Check existing position for take-profit at mean (mid-band)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "long_tp_mid_band_reached", "close": curr_close, "mid": mid, "pct_b": pct_b}
                )
            elif direction == "short" and curr_close <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "short_tp_mid_band_reached", "close": curr_close, "mid": mid, "pct_b": pct_b}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter to avoid mean reverting into high crisis/meltdown dislocations
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Event: Prior bar closed outside the band, current bar closes back inside with rejection
        long_event = (prev_pct_b < 0.0) and (pct_b >= 0.05) and (curr_close > ctx.bar.open)
        short_event = (prev_pct_b > 1.0) and (pct_b <= 0.95) and (curr_close < ctx.bar.open)

        if long_event:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_failed_lower_break_reversion",
                    "prev_pct_b": prev_pct_b,
                    "pct_b": pct_b,
                    "close": curr_close,
                    "lower": lower,
                    "mid": mid
                }
            )

        if short_event:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_failed_upper_break_reversion",
                    "prev_pct_b": prev_pct_b,
                    "pct_b": pct_b,
                    "close": curr_close,
                    "upper": upper,
                    "mid": mid
                }
            )

        return None