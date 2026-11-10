from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBReversion(Strategy):
    METADATA = {
        "name": "SolPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.std_dev = 2.0
        self.cooldown_bars = 6
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

        # Filter crisis or extreme meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "regime": market_regime}
                )
            return None

        # Current bar Bollinger Bands
        curr_bb = self._bollinger(closes[:-1] + [closes[-1]], self.period, self.std_dev)
        # Previous bar Bollinger Bands
        prev_bb = self._bollinger(closes[:-1], self.period, self.std_dev)

        if curr_bb is None or prev_bb is None:
            return None

        curr_mid, curr_upper, curr_lower = curr_bb
        prev_mid, prev_upper, prev_lower = prev_bb

        curr_width = curr_upper - curr_lower
        prev_width = prev_upper - prev_lower
        if curr_width <= 0 or prev_width <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        curr_pct_b = (curr_close - curr_lower) / curr_width
        prev_pct_b = (prev_close - prev_lower) / prev_width

        # In-position management
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion target: Mid-band
            if direction == "long" and curr_close >= curr_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "mid_band_target_hit_long",
                        "pct_b": curr_pct_b,
                        "close": curr_close,
                        "mid": curr_mid
                    }
                )
            elif direction == "short" and curr_close <= curr_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "mid_band_target_hit_short",
                        "pct_b": curr_pct_b,
                        "close": curr_close,
                        "mid": curr_mid
                    }
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Band expansion check: Avoid trading during extreme compression chop
        band_spread_pct = curr_width / curr_mid
        if band_spread_pct < 0.015:
            return None

        # Long Entry: Previous closed below lower band (%B < 0), Current closed back inside (%B >= 0.05)
        # Avoid entering if current already closed way too high (%B > 0.45)
        if prev_pct_b < 0.0 and 0.05 <= curr_pct_b <= 0.45:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_rejection_long",
                    "prev_pct_b": prev_pct_b,
                    "curr_pct_b": curr_pct_b,
                    "close": curr_close,
                    "lower_band": curr_lower,
                    "mid_band": curr_mid
                }
            )

        # Short Entry: Previous closed above upper band (%B > 1), Current closed back inside (%B <= 0.95)
        # Avoid entering if current already dropped too far (%B < 0.55)
        if prev_pct_b > 1.0 and 0.55 <= curr_pct_b <= 0.95:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_rejection_short",
                    "prev_pct_b": prev_pct_b,
                    "curr_pct_b": curr_pct_b,
                    "close": curr_close,
                    "upper_band": curr_upper,
                    "mid_band": curr_mid
                }
            )

        return None