from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _bollinger_bands(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _pct_b(self, close, lower, upper):
        width = upper - lower
        if width <= 1e-8:
            return 0.5
        return (close - lower) / width

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 3)
        lows = ctx.lows(self.period + 3)
        highs = ctx.highs(self.period + 3)
        opens = ctx.opens(self.period + 3)

        if len(closes) < self.period + 2:
            return None

        # Current and previous bar Bollinger Bands
        mean_curr, upper_curr, lower_curr = self._bollinger_bands(closes, self.period, self.num_std)
        mean_prev, upper_prev, lower_prev = self._bollinger_bands(closes[:-1], self.period, self.num_std)

        if mean_curr is None or mean_prev is None:
            return None

        pct_b_curr = self._pct_b(closes[-1], lower_curr, upper_curr)
        pct_b_prev = self._pct_b(closes[-2], lower_prev, upper_prev)

        curr_close = closes[-1]
        curr_open = opens[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # In-position management: Exit at mid-band (SMA)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= mean_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_mid_band_reached_long",
                        "close": curr_close,
                        "mid": mean_curr,
                        "pct_b": pct_b_curr,
                    }
                )
            elif direction == "short" and curr_close <= mean_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_mid_band_reached_short",
                        "close": curr_close,
                        "mid": mean_curr,
                        "pct_b": pct_b_curr,
                    }
                )
            return None

        # Cooldown guard to avoid churn and overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme panic/meltdown periods
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime == "MELTDOWN":
            return None

        # Strict event boundary for Long:
        # 1. Previous bar broke strictly below lower band (pct_b_prev < 0.0 or prev_low < lower_prev)
        # 2. Current bar closes cleanly back inside (pct_b_curr >= 0.05 and pct_b_curr <= 0.45)
        # 3. Bullish candle confirmation (close > open)
        long_reentry = (pct_b_prev < 0.0 or prev_low < lower_prev) and (0.05 <= pct_b_curr <= 0.45) and (curr_close > curr_open)

        # Strict event boundary for Short:
        # 1. Previous bar broke strictly above upper band (pct_b_prev > 1.0 or prev_high > upper_prev)
        # 2. Current bar closes cleanly back inside (pct_b_curr <= 0.95 and pct_b_curr >= 0.55)
        # 3. Bearish candle confirmation (close < open)
        short_reentry = (pct_b_prev > 1.0 or prev_high > upper_prev) and (0.55 <= pct_b_curr <= 0.95) and (curr_close < curr_open)

        if long_reentry:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=320.0,
                take_profit_bps=450.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "pctb_failed_breakdown_reentry",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "lower_band": lower_curr,
                    "close": curr_close,
                    "mid_band": mean_curr,
                }
            )

        if short_reentry:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=320.0,
                take_profit_bps=450.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "pctb_failed_breakout_reentry",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "upper_band": upper_curr,
                    "close": curr_close,
                    "mid_band": mean_curr,
                }
            )

        return None