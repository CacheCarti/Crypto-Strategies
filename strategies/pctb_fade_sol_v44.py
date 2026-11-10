from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _bollinger(self, closes: list, period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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

        # Compute current bands
        mid, upper, lower = self._bollinger(closes[-self.period:], self.period, self.num_std)
        if mid is None or upper is None or lower is None or upper == lower:
            return None

        # Compute previous bands to check prior penetration
        prev_closes = closes[-(self.period + 1):-1]
        p_mid, p_upper, p_lower = self._bollinger(prev_closes, self.period, self.num_std)
        if p_mid is None or p_upper is None or p_lower is None or p_upper == p_lower:
            return None

        pct_b = (closes[-1] - lower) / (upper - lower)
        prev_pct_b = (closes[-2] - p_lower) / (p_upper - p_lower)

        # In-position logic: mean-reversion exit at mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and closes[-1] >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_tp_mid_band_reached", "close": closes[-1], "mid": mid, "pct_b": pct_b}
                )
            elif pos_dir == "short" and closes[-1] <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_tp_mid_band_reached", "close": closes[-1], "mid": mid, "pct_b": pct_b}
                )
            return None

        # Cooldown guard to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Skip extreme crisis regimes to avoid stepping in front of trending liquidation cascades
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # RSI filter to ensure higher conviction on deep exhaustion
        all_closes = ctx.closes(30)
        rsi = self._rsi(all_closes, 14)
        if rsi is None:
            return None

        # Long Entry: prior bar penetrated lower band (prev_pct_b < 0.0), current bar closes back inside (0.05 <= pct_b < 0.40)
        # Deep exhaustion confirmed by RSI < 40 to avoid fading early downward expansions
        if prev_pct_b < 0.0 and 0.05 <= pct_b < 0.40 and rsi < 40.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_lower_band_break_reentry",
                    "pct_b": pct_b,
                    "prev_pct_b": prev_pct_b,
                    "rsi": rsi,
                    "mid": mid,
                    "lower": lower
                }
            )

        # Short Entry: prior bar pierced upper band (prev_pct_b > 1.0), current bar closes back inside (0.60 < pct_b <= 0.95)
        # Deep exhaustion confirmed by RSI > 60 to avoid shorting sustained momentum breakouts
        if prev_pct_b > 1.0 and 0.60 < pct_b <= 0.95 and rsi > 60.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_upper_band_break_reentry",
                    "pct_b": pct_b,
                    "prev_pct_b": prev_pct_b,
                    "rsi": rsi,
                    "mid": mid,
                    "upper": upper
                }
            )

        return None