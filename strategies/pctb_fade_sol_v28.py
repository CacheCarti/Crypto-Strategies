from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PctBFadeSol(Strategy):
    METADATA = {
        "name": "PctBFadeSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.period + 3)
        lows = ctx.lows(self.period + 3)
        highs = ctx.highs(self.period + 3)

        if len(closes) < self.period + 2:
            return None

        # Current and previous Bollinger Bands
        mid, upper, lower = self._bollinger(closes, self.period, self.num_std)
        prev_mid, prev_upper, prev_lower = self._bollinger(closes[:-1], self.period, self.num_std)

        if lower is None or prev_lower is None or upper == lower or prev_upper == prev_lower:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        pct_b = (curr_close - lower) / (upper - lower)
        bandwidth = (upper - lower) / mid

        # Cooldown check
        in_cooldown = (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars

        # Check existing position for mid-band reversion exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_tp_mid_band_reached", "pct_b": pct_b, "mid": mid, "close": curr_close}
                )
            elif pos_dir == "short" and curr_close <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_tp_mid_band_reached", "pct_b": pct_b, "mid": mid, "close": curr_close}
                )
            return None

        if in_cooldown:
            return None

        # Avoid entries when bands are severely compressed (breakout regime, not range fade)
        if bandwidth < 0.025:
            return None

        # Market regime filter: avoid fading against crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long: previous or current candle dipped below lower band, but current candle closes back inside (0.05 <= pct_b <= 0.40)
        pierced_lower = (prev_close < prev_lower) or (prev_low < prev_lower) or (curr_low < lower)
        closed_inside_lower = (curr_close > lower) and (0.05 <= pct_b <= 0.40)

        if pierced_lower and closed_inside_lower:
            conf = min(0.85, max(0.55, 0.5 + (0.40 - pct_b)))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=360.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_lower_band_break_reversion",
                    "pct_b": pct_b,
                    "bandwidth": bandwidth,
                    "lower": lower,
                    "mid": mid,
                    "close": curr_close
                }
            )

        # Short: previous or current candle spiked above upper band, but current candle closes back inside (0.60 <= pct_b <= 0.95)
        pierced_upper = (prev_close > prev_upper) or (prev_high > prev_upper) or (curr_high > upper)
        closed_inside_upper = (curr_close < upper) and (0.60 <= pct_b <= 0.95)

        if pierced_upper and closed_inside_upper:
            conf = min(0.85, max(0.55, 0.5 + (pct_b - 0.60)))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=360.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_upper_band_break_reversion",
                    "pct_b": pct_b,
                    "bandwidth": bandwidth,
                    "upper": upper,
                    "mid": mid,
                    "close": curr_close
                }
            )

        return None