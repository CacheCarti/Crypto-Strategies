from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeMeanReversion(Strategy):
    METADATA = {
        "name": "SOL PctB Fade Cross-Asset Confirmation",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_mult = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger(self, closes, period=20, mult=2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + mult * std, mean - mult * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 2)
        if len(closes) < self.bb_period + 2:
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Current and previous Bollinger Bands
        curr_mid, curr_upper, curr_lower = self._bollinger(closes[-self.bb_period:], self.bb_period, self.bb_mult)
        prev_mid, prev_upper, prev_lower = self._bollinger(closes[-self.bb_period - 1:-1], self.bb_period, self.bb_mult)

        if curr_mid is None or prev_mid is None:
            return None

        curr_width = curr_upper - curr_lower
        prev_width = prev_upper - prev_lower
        if curr_width <= 0 or prev_width <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        curr_pct_b = (curr_close - curr_lower) / curr_width
        prev_pct_b = (prev_close - prev_lower) / prev_width

        # Cross-asset confirmation: avoid fading against broad market trend
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # Exits if currently holding a position
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= curr_mid:
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": "long_hit_midband_exit",
                    "pct_b": round(curr_pct_b, 3),
                    "close": curr_close,
                    "mid": round(curr_mid, 2)
                })
            elif direction == "short" and curr_close <= curr_mid:
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": "short_hit_midband_exit",
                    "pct_b": round(curr_pct_b, 3),
                    "close": curr_close,
                    "mid": round(curr_mid, 2)
                })
            return None

        # Do not initiate new entries during severe market crises
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility condition: prevent entering into extreme low-vol compression pinch
        bandwidth = curr_width / curr_mid
        if bandwidth < 0.015:
            return None

        # Entry logic: PctB hook back inside from piercing bands
        # Long setup: Previous bar closed below lower band (%B < 0), current bar reclaimed inside (%B >= 0 and <= 0.35)
        if prev_pct_b < 0.0 and 0.0 <= curr_pct_b <= 0.35:
            if btc_ret >= -3.5:  # BTC is not in severe free-fall
                confidence = 0.70 if btc_ret > 0 else 0.55
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "pctb_lower_band_reclaim",
                        "prev_pct_b": round(prev_pct_b, 3),
                        "curr_pct_b": round(curr_pct_b, 3),
                        "btc_ret_pct": round(btc_ret, 2),
                        "bandwidth": round(bandwidth, 4)
                    }
                )

        # Short setup: Previous bar closed above upper band (%B > 1.0), current bar closed back inside (%B <= 1.0 and >= 0.65)
        if prev_pct_b > 1.0 and 0.65 <= curr_pct_b <= 1.0:
            if btc_ret <= 3.5:  # BTC is not in a parabolic squeeze
                confidence = 0.70 if btc_ret < 0 else 0.55
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "pctb_upper_band_fade",
                        "prev_pct_b": round(prev_pct_b, 3),
                        "curr_pct_b": round(curr_pct_b, 3),
                        "btc_ret_pct": round(btc_ret, 2),
                        "bandwidth": round(bandwidth, 4)
                    }
                )

        return None