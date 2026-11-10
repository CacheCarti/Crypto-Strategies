from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBollingerPercentBFade(Strategy):
    METADATA = {
        "name": "SolBollingerPercentBFade",
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
        closes = ctx.closes(self.bb_period + 2)
        lows = ctx.lows(self.bb_period + 2)
        highs = ctx.highs(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes to prevent toxic flow steamrolling fades
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Current Bollinger Bands
        mid, upper, lower = self._bollinger(closes[:-1], self.bb_period, self.bb_std)
        if mid is None or upper == lower:
            return None

        # Previous bar bands for accurate breakout tracking
        prev_mid, prev_upper, prev_lower = self._bollinger(closes[:-2], self.bb_period, self.bb_std)
        if prev_mid is None or prev_upper == prev_lower:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_band_width = upper - lower

        curr_pct_b = (curr_close - lower) / curr_band_width
        prev_pct_b = (prev_close - prev_lower) / (prev_upper - prev_lower)

        # Position management: Target mean reversion exit at mid-band
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= mid:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "tp_mid_band_reached",
                    "curr_close": curr_close,
                    "mid": mid,
                    "pct_b": curr_pct_b
                })
            elif direction == "short" and curr_close <= mid:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "tp_mid_band_reached",
                    "curr_close": curr_close,
                    "mid": mid,
                    "pct_b": curr_pct_b
                })
            return None

        # High-conviction entry conditions:
        # Long: Previous bar breached below lower band (prev_pct_b < 0.0), current bar strongly reclaims inside (%B > 0.10 and < 0.45)
        # Low of prev bar pierced, forming a clear rejected wick
        if prev_pct_b < 0.0 and 0.10 <= curr_pct_b < 0.45 and curr_close > prev_close:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_rejection_reclaim",
                    "pct_b": curr_pct_b,
                    "prev_pct_b": prev_pct_b,
                    "lower_band": lower,
                    "mid_band": mid,
                    "curr_close": curr_close
                }
            )

        # Short: Previous bar breached above upper band (prev_pct_b > 1.0), current bar closes back inside (%B < 0.90 and > 0.55)
        if prev_pct_b > 1.0 and 0.55 < curr_pct_b <= 0.90 and curr_close < prev_close:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_rejection_reclaim",
                    "pct_b": curr_pct_b,
                    "prev_pct_b": prev_pct_b,
                    "upper_band": upper,
                    "mid_band": mid,
                    "curr_close": curr_close
                }
            )

        return None