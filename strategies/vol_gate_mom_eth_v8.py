from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class RegimeGatedMomentum(Strategy):
    METADATA = {
        "name": "RegimeGatedMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 24
        self.vol_period = 24
        self.baseline_period = 168
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.last_signal_bar = -999
        self.min_vol_ratio = 1.15
        self.breakout_bps = 25.0

    def _stddev(self, values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)

    def _median(self, values: List[float]) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        mid = n // 2
        if n % 2 == 1:
            return sorted_vals[mid]
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0

    def _sma(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.baseline_period + self.vol_period + 10
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        # Calculate bar-to-bar returns
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        if len(returns) < self.baseline_period + self.vol_period:
            return None

        # Build rolling realized volatility series
        vol_history = []
        for i in range(self.vol_period, len(returns) + 1):
            window = returns[i - self.vol_period:i]
            vol_history.append(self._stddev(window))

        if len(vol_history) < self.baseline_period:
            return None

        current_vol = vol_history[-1]
        baseline_slice = vol_history[-self.baseline_period:]
        median_vol = self._median(baseline_slice)

        if median_vol <= 1e-8:
            return None

        vol_ratio = current_vol / median_vol
        is_active_regime = vol_ratio >= self.min_vol_ratio

        current_close = closes[-1]
        prev_close = closes[-2]

        sma_current = self._sma(closes, self.fast_period)
        sma_prev = self._sma(closes[:-1], self.fast_period)

        if sma_current is None or sma_prev is None:
            return None

        # Manage existing open position
        if ctx.has_position():
            direction = ctx.position_direction()
            should_flat = False
            flat_reason = ""

            # Exit if regime collapses into extreme dormancy or clear reversal through SMA
            if vol_ratio < 0.80:
                should_flat = True
                flat_reason = "volatility_exhaustion_flat"
            elif direction == "long" and current_close < sma_current * 0.995:
                should_flat = True
                flat_reason = "trend_broken_long_exit"
            elif direction == "short" and current_close > sma_current * 1.005:
                should_flat = True
                flat_reason = "trend_broken_short_exit"

            if should_flat:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": flat_reason,
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 5),
                        "median_vol": round(median_vol, 5),
                        "close": current_close,
                        "sma": round(sma_current, 2),
                    }
                )
            return None

        # Mandatory cooldown to prevent overtrading and fee drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Active volatility regime requirement
        if not is_active_regime:
            return None

        # Confidence scaled by magnitude of active volatility above baseline
        confidence = min(0.90, max(0.55, 0.55 + 0.25 * (vol_ratio - self.min_vol_ratio)))

        # Momentum confirmation with minimum breakout distance (in bps) to filter chop
        buffer_ratio = self.breakout_bps / 10000.0
        bullish_trigger = (prev_close <= sma_prev) and (current_close > sma_current * (1.0 + buffer_ratio))
        bearish_trigger = (prev_close >= sma_prev) and (current_close < sma_current * (1.0 - buffer_ratio))

        if bullish_trigger and ctx.bar_index != self.last_signal_bar:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "regime_gated_momentum_long_cross",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 5),
                    "median_vol": round(median_vol, 5),
                    "close": current_close,
                    "sma": round(sma_current, 2),
                }
            )

        if bearish_trigger and ctx.bar_index != self.last_signal_bar:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "regime_gated_momentum_short_cross",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 5),
                    "median_vol": round(median_vol, 5),
                    "close": current_close,
                    "sma": round(sma_current, 2),
                }
            )

        return None