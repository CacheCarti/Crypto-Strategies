from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFade(Strategy):
    METADATA = {
        "name": "SolPctBFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.rsi_period = 14

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
        closes = ctx.closes(self.period + self.rsi_period + 2)
        if len(closes) < self.period + 2:
            return None

        # Calculate current Bollinger Bands
        mid, upper, lower = self._bollinger(closes, self.period, self.num_std)
        if mid is None or upper is None or lower is None or upper == lower:
            return None

        # Prior bar Bollinger Bands
        prior_closes = closes[:-1]
        prior_mid, prior_upper, prior_lower = self._bollinger(prior_closes, self.period, self.num_std)
        if prior_mid is None or prior_upper is None or prior_lower is None or prior_upper == prior_lower:
            return None

        current_close = closes[-1]
        prior_close = prior_closes[-1]

        pct_b = (current_close - lower) / (upper - lower)
        prior_pct_b = (prior_close - prior_lower) / (prior_upper - prior_lower)

        # In-position logic: check for mean-reversion target (mid-band)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_mid_band_target_reached", "close": current_close, "mid": mid, "pct_b": pct_b}
                )
            if direction == "short" and current_close <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_mid_band_target_reached", "close": current_close, "mid": mid, "pct_b": pct_b}
                )
            return None

        # Cooldown guard to avoid churn and overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, self.rsi_period)

        # Long Setup: Was below lower band (<0.0), now closed back inside (>0.0 and <=0.40), RSI oversold confirmation
        if prior_pct_b < 0.0 and 0.0 <= pct_b <= 0.40 and rsi < 42.0:
            confidence = min(0.85, 0.55 + max(0.0, (40.0 - rsi) / 100.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pctb_lower_reentry_reversion",
                    "pct_b": round(pct_b, 4),
                    "prior_pct_b": round(prior_pct_b, 4),
                    "rsi": round(rsi, 2),
                    "mid": round(mid, 2),
                    "lower": round(lower, 2)
                }
            )

        # Short Setup: Was above upper band (>1.0), now closed back inside (<1.0 and >=0.60), RSI overbought confirmation
        if prior_pct_b > 1.0 and 0.60 <= pct_b <= 1.0 and rsi > 58.0:
            confidence = min(0.85, 0.55 + max(0.0, (rsi - 60.0) / 100.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "pctb_upper_reentry_reversion",
                    "pct_b": round(pct_b, 4),
                    "prior_pct_b": round(prior_pct_b, 4),
                    "rsi": round(rsi, 2),
                    "mid": round(mid, 2),
                    "upper": round(upper, 2)
                }
            )

        return None