from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class PercentBMeanReversion(Strategy):
    METADATA = {
        "name": "SOL Percent B Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 24
        self.bb_std = 2.25
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -999

    def _bollinger(self, closes, period: int, num_std: float):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return 50.0
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
        closes = ctx.closes(self.bb_period + 5)
        if len(closes) < self.bb_period + 2:
            return None

        mid_curr, up_curr, low_curr = self._bollinger(closes, self.bb_period, self.bb_std)
        mid_prev, up_prev, low_prev = self._bollinger(closes[:-1], self.bb_period, self.bb_std)

        if mid_curr is None or mid_prev is None:
            return None

        bw_curr = up_curr - low_curr
        bw_prev = up_prev - low_prev
        if bw_curr <= 0 or bw_prev <= 0:
            return None

        c_curr = closes[-1]
        c_prev = closes[-2]

        pct_b_curr = (c_curr - low_curr) / bw_curr
        pct_b_prev = (c_prev - low_prev) / bw_prev

        rsi_val = self._rsi(closes, self.rsi_period)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")

        # 1. Manage Active Positions (Mid-band target exit)
        if ctx.has_position():
            pos_dir = ctx.position_direction()

            if pos_dir == "long" and c_curr >= mid_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_mid_band_target_reached",
                        "close": round(c_curr, 2),
                        "mid_band": round(mid_curr, 2),
                        "pct_b": round(pct_b_curr, 4),
                        "rsi": round(rsi_val, 2),
                    },
                )

            if pos_dir == "short" and c_curr <= mid_curr:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_mid_band_target_reached",
                        "close": round(c_curr, 2),
                        "mid_band": round(mid_curr, 2),
                        "pct_b": round(pct_b_curr, 4),
                        "rsi": round(rsi_val, 2),
                    },
                )

            return None

        # 2. Strict multi-bar cooldown after exit to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid choppy crisis regimes
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        # 3. High-conviction Long Entry: clear dip below band (%B < -0.02) and recovery inside with RSI oversold filter
        if pct_b_prev < -0.02 and 0.05 <= pct_b_curr < 0.45 and c_curr < mid_curr:
            if rsi_val <= 42.0:
                conf = 0.70 if fear_greed < 45 else 0.60
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_lower_band_break_reentry",
                        "pct_b_prev": round(pct_b_prev, 4),
                        "pct_b_curr": round(pct_b_curr, 4),
                        "rsi": round(rsi_val, 2),
                        "fear_greed": fear_greed,
                        "close": round(c_curr, 2),
                        "mid_band": round(mid_curr, 2),
                    },
                )

        # 4. High-conviction Short Entry: clear pierce above band (%B > 1.02) and rejection inside with RSI overbought filter
        if pct_b_prev > 1.02 and 0.55 < pct_b_curr <= 0.95 and c_curr > mid_curr:
            if rsi_val >= 58.0:
                conf = 0.70 if fear_greed > 55 else 0.60
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_upper_band_break_reentry",
                        "pct_b_prev": round(pct_b_prev, 4),
                        "pct_b_curr": round(pct_b_curr, 4),
                        "rsi": round(rsi_val, 2),
                        "fear_greed": fear_greed,
                        "close": round(c_curr, 2),
                        "mid_band": round(mid_curr, 2),
                    },
                )

        return None