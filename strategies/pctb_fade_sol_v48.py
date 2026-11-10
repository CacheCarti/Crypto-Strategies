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
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.period + 5)
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

        # Compute %B for current and previous bar
        pct_b = (closes[-1] - lower) / band_width
        pct_b_prev = (closes[-2] - lower_prev) / band_width_prev

        # Check existing position for target exit at mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and closes[-1] >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_mid_band_long", "pct_b": pct_b, "mid": mid, "close": closes[-1]}
                )
            elif pos_dir == "short" and closes[-1] <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_mid_band_short", "pct_b": pct_b, "mid": mid, "close": closes[-1]}
                )
            return None

        # Post-exit cooldown gate to prevent overtrading and fee drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Secondary momentum check to ensure exhaustion
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Avoid entering during extreme crisis volatility
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # State transition: Long setup when previous bar closed below lower band and current recovers inside
        if pct_b_prev < 0.0 and pct_b >= 0.0 and rsi < 42.0:
            conf = min(0.85, 0.55 + max(0.0, (40.0 - rsi) / 40.0 * 0.3))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_breakdown_reentry",
                    "pct_b": pct_b,
                    "pct_b_prev": pct_b_prev,
                    "rsi": rsi,
                    "mid": mid
                }
            )

        # State transition: Short setup when previous bar closed above upper band and current falls back inside
        if pct_b_prev > 1.0 and pct_b <= 1.0 and rsi > 58.0:
            conf = min(0.85, 0.55 + max(0.0, (rsi - 60.0) / 40.0 * 0.3))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_breakout_reentry",
                    "pct_b": pct_b,
                    "pct_b_prev": pct_b_prev,
                    "rsi": rsi,
                    "mid": mid
                }
            )

        return None