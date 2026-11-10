from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctbReversion(Strategy):
    METADATA = {
        "name": "SolPctbReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.num_std = 2.0
        self.rsi_period = 14
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
        closes = ctx.closes(self.bb_period + 2)
        lows = ctx.lows(2)
        highs = ctx.highs(2)
        
        if len(closes) < self.bb_period + 1:
            return None

        # Compute current bands on the last 20 completed closes
        mid, upper, lower = self._bollinger(closes, self.bb_period, self.num_std)
        if mid is None or upper is None or lower is None or upper == lower:
            return None

        band_width = upper - lower
        curr_close = closes[-1]
        pct_b = (curr_close - lower) / band_width

        # In-position dynamic mean-reversion exit checks (mid-band touch)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_mid_band_target_reached", "pct_b": pct_b, "mid": mid, "close": curr_close}
                )
            elif direction == "short" and curr_close <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_mid_band_target_reached", "pct_b": pct_b, "mid": mid, "close": curr_close}
                )
            return None

        # Cooldown guard to avoid overtrading and consecutive entries
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter to avoid mean-reversion in extreme panic
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Previous bar's bands to verify the breach and re-entry
        prev_closes = closes[:-1]
        prev_mid, prev_upper, prev_lower = self._bollinger(prev_closes, self.bb_period, self.num_std)
        if prev_mid is None:
            return None

        rsi_closes = ctx.closes(self.rsi_period + 2)
        rsi = self._rsi(rsi_closes, self.rsi_period) or 50.0

        # Long Reversion: Prior bar dipped below lower band, current close reclaimed inside
        prior_pierced_lower = lows[-2] < prev_lower or prev_closes[-1] < prev_lower
        reclaimed_lower = curr_close > lower and pct_b > 0.05 and pct_b < 0.40
        if prior_pierced_lower and reclaimed_lower and rsi < 48.0:
            confidence = min(0.85, 0.55 + max(0.0, (40.0 - rsi) / 100.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_pierce_reclaim",
                    "pct_b": pct_b,
                    "rsi": rsi,
                    "lower": lower,
                    "close": curr_close
                }
            )

        # Short Reversion: Prior bar spiked above upper band, current close fell back inside
        prior_pierced_upper = highs[-2] > prev_upper or prev_closes[-1] > prev_upper
        reclaimed_upper = curr_close < upper and pct_b < 0.95 and pct_b > 0.60
        if prior_pierced_upper and reclaimed_upper and rsi > 52.0:
            confidence = min(0.85, 0.55 + max(0.0, (rsi - 60.0) / 100.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_pierce_reclaim",
                    "pct_b": pct_b,
                    "rsi": rsi,
                    "upper": upper,
                    "close": curr_close
                }
            )

        return None