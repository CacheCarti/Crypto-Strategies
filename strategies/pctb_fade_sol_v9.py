from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class PercentBReversion(Strategy):
    METADATA = {
        "name": "PercentBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 24
        self.num_std = 2.4
        self.rsi_period = 14
        self.cooldown_bars = 12
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
        needed_bars = max(self.bb_period, self.rsi_period) + 3
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        # Bollinger calculations
        mid, upper, lower = self._bollinger(closes, self.bb_period, self.num_std)
        if mid is None or upper is None or lower is None or upper == lower:
            return None

        curr_close = closes[-1]
        bandwidth = upper - lower
        pct_b = (curr_close - lower) / bandwidth

        # Previous bar Bollinger calculations
        prev_closes = closes[:-1]
        p_mid, p_upper, p_lower = self._bollinger(prev_closes, self.bb_period, self.num_std)
        if p_mid is None or p_upper is None or p_lower is None or p_upper == p_lower:
            return None

        prev_close = prev_closes[-1]
        p_bandwidth = p_upper - p_lower
        prev_pct_b = (prev_close - p_lower) / p_bandwidth

        # RSI confirmation
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position Management / Reversion Exit to Mid-Band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (curr_close >= mid or pct_b >= 0.50):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_tp_midband_reached",
                        "pct_b": round(pct_b, 4),
                        "close": round(curr_close, 4),
                        "mid": round(mid, 4),
                        "rsi": round(rsi, 2),
                    },
                )
            elif pos_dir == "short" and (curr_close <= mid or pct_b <= 0.50):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_tp_midband_reached",
                        "pct_b": round(pct_b, 4),
                        "close": round(curr_close, 4),
                        "mid": round(mid, 4),
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # Strict multi-bar cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime protection: skip entries during high turbulence or market stress
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL") or crisis_score > 0.45:
            return None

        # Long Entry: Clear band puncture followed by inside-band close + oversold RSI
        if prev_pct_b < -0.02 and 0.04 <= pct_b <= 0.32 and rsi <= 38.0:
            confidence = min(0.95, 0.60 + max(0.0, (38.0 - rsi) / 40.0))
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bollinger_pct_b_oversold_reversal",
                    "pct_b": round(pct_b, 4),
                    "prev_pct_b": round(prev_pct_b, 4),
                    "rsi": round(rsi, 2),
                    "close": round(curr_close, 4),
                    "lower": round(lower, 4),
                    "mid": round(mid, 4),
                },
            )

        # Short Entry: Clear band puncture above followed by inside-band close + overbought RSI
        if prev_pct_b > 1.02 and 0.68 <= pct_b <= 0.96 and rsi >= 62.0:
            confidence = min(0.95, 0.60 + max(0.0, (rsi - 62.0) / 40.0))
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bollinger_pct_b_overbought_reversal",
                    "pct_b": round(pct_b, 4),
                    "prev_pct_b": round(prev_pct_b, 4),
                    "rsi": round(rsi, 2),
                    "close": round(curr_close, 4),
                    "upper": round(upper, 4),
                    "mid": round(mid, 4),
                },
            )

        return None