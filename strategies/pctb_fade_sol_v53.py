from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeConfirmed(Strategy):
    METADATA = {
        "name": "SolPctBFadeConfirmed",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
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
        closes = ctx.closes(self.period + 3)
        if len(closes) < self.period + 2:
            return None

        # Compute current and previous Bollinger bands
        mid_curr, upper_curr, lower_curr = self._bollinger(closes, self.period, self.num_std)
        mid_prev, upper_prev, lower_prev = self._bollinger(closes[:-1], self.period, self.num_std)

        if mid_curr is None or mid_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        width_curr = upper_curr - lower_curr
        width_prev = upper_prev - lower_prev

        if width_curr <= 1e-6 or width_prev <= 1e-6:
            return None

        pct_b_curr = (curr_close - lower_curr) / width_curr
        pct_b_prev = (prev_close - lower_prev) / width_prev

        # Active position management: take profit or mean-reversion exit at mid-band
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_tp_mid_band_reached", "close": curr_close, "mid": mid_curr, "pct_b": pct_b_curr}
                )
            elif direction == "short" and curr_close <= mid_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_tp_mid_band_reached", "close": curr_close, "mid": mid_curr, "pct_b": pct_b_curr}
                )
            return None

        # Enforce post-exit cooldown to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid fading strong trending breakouts during market crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility bandwidth expansion check (relative bandwidth check vs mean price)
        bandwidth_pct = width_curr / mid_curr
        if bandwidth_pct < 0.015:  # bands too tight, breakout prone / chop
            return None

        # Cross-asset anchor: use BTC 24h momentum to avoid fading against broader crypto trend
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # %B Rejection & Re-entry:
        # Long: previous bar closed outside lower band (%B < 0), current bar closes back inside (%B >= 0)
        long_reentry = (pct_b_prev < 0.0) and (pct_b_curr >= 0.0) and (curr_close > closes[-2])
        # Short: previous bar closed outside upper band (%B > 1), current bar closes back inside (%B <= 1)
        short_reentry = (pct_b_prev > 1.0) and (pct_b_curr <= 1.0) and (curr_close < closes[-2])

        if long_reentry and btc_ret > -4.5:
            confidence = 0.65 if btc_ret >= 0.0 else 0.55
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_lower_band_reentry",
                    "pct_b_curr": pct_b_curr,
                    "pct_b_prev": pct_b_prev,
                    "btc_ret": btc_ret,
                    "bandwidth_pct": bandwidth_pct,
                }
            )

        if short_reentry and btc_ret < 4.5:
            confidence = 0.65 if btc_ret <= 0.0 else 0.55
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_upper_band_reentry",
                    "pct_b_curr": pct_b_curr,
                    "pct_b_prev": pct_b_prev,
                    "btc_ret": btc_ret,
                    "bandwidth_pct": bandwidth_pct,
                }
            )

        return None