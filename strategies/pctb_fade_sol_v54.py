from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFadeSelective",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.rsi_period = 14
        self.last_exit_bar = -999
        self.cooldown_bars = 5

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        bb_curr = self._bollinger(closes, self.period, self.num_std)
        bb_prev = self._bollinger(closes[:-1], self.period, self.num_std)
        if bb_curr is None or bb_prev is None:
            return None

        mid, upper, lower = bb_curr
        _, upper_prev, lower_prev = bb_prev

        band_width = upper - lower
        band_width_prev = upper_prev - lower_prev
        if band_width <= 0 or band_width_prev <= 0:
            return None

        pct_b_curr = (closes[-1] - lower) / band_width
        pct_b_prev = (closes[-2] - lower_prev) / band_width_prev

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and closes[-1] >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_tp_midband", "close": closes[-1], "mid": mid, "pct_b": pct_b_curr}
                )
            elif pos_dir == "short" and closes[-1] <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_tp_midband", "close": closes[-1], "mid": mid, "pct_b": pct_b_curr}
                )
            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries during crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Long: previous close was strictly below lower band (%B < 0), current close reclaimed inside
        # Selective gate: RSI confirmed oversold (< 38)
        if pct_b_prev < -0.05 and pct_b_curr >= 0.05 and closes[-1] < mid and rsi < 38.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=280.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "pctb_lower_reclaim",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "rsi": rsi,
                    "lower_band": lower,
                    "close": closes[-1]
                }
            )

        # Short: previous close was strictly above upper band (%B > 1), current close closed back inside
        # Selective gate: RSI confirmed overbought (> 62)
        if pct_b_prev > 1.05 and pct_b_curr <= 0.95 and closes[-1] > mid and rsi > 62.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=280.0,
                take_profit_bps=340.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "pctb_upper_reclaim",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "rsi": rsi,
                    "upper_band": upper,
                    "close": closes[-1]
                }
            )

        return None