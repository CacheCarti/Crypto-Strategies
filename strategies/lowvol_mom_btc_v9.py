from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class LowVolTrendFollower(Strategy):
    METADATA = {
        "name": "LowVolTrendFollower",
        "domain": "btc_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 21
        self.slow_ema_period = 55
        self.vol_period = 20
        self.vol_lookback = 180
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _realized_vol(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append((closes[i] - prev) / prev)
        if not returns:
            return None
        mean_ret = sum(returns) / len(returns)
        var = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.vol_lookback + self.vol_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Calculate current and historical realized volatility to find median
        vol_samples = []
        step = 3
        for start_idx in range(len(closes) - self.vol_lookback, len(closes) + 1, step):
            sub_closes = closes[:start_idx]
            vol = self._realized_vol(sub_closes, self.vol_period)
            if vol is not None:
                vol_samples.append(vol)

        if len(vol_samples) < 15:
            return None

        current_vol = vol_samples[-1]
        sorted_vols = sorted(vol_samples)
        median_vol = sorted_vols[len(sorted_vols) // 2]

        is_calm_tape = current_vol < (median_vol * 0.95)
        is_vol_spike = current_vol > (median_vol * 1.55)

        # Indicator calculations
        ema_fast = self._ema(closes, self.fast_ema_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_ema_period)
        ema_slow = self._ema(closes, self.slow_ema_period)

        if ema_fast is None or ema_fast_prev is None or ema_slow is None:
            return None

        ema_slope = (ema_fast - ema_fast_prev) / ema_fast_prev
        price = ctx.bar.close
        prev_price = closes[-2]

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit conditions when holding a position
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            if is_vol_spike:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "exit_volatility_spike",
                        "current_vol": current_vol,
                        "median_vol": median_vol,
                        "bars_held": bars_held,
                    }
                )
            if pos_dir == "long" and (price < ema_fast and ema_slope < -0.0002):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_long_trend_break",
                        "price": price,
                        "ema_fast": ema_fast,
                        "ema_slope": ema_slope,
                    }
                )
            if pos_dir == "short" and (price > ema_fast and ema_slope > 0.0002):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_short_trend_break",
                        "price": price,
                        "ema_fast": ema_fast,
                        "ema_slope": ema_slope,
                    }
                )
            return None

        # Check cooldown before opening new positions
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry triggers: Price transitioning across EMA21 in calm tape with slope alignment
        long_cross = (prev_price <= ema_fast_prev and price > ema_fast) or (price > ema_fast and ema_slope > 0.0004 and price > ema_slow)
        short_cross = (prev_price >= ema_fast_prev and price < ema_fast) or (price < ema_fast and ema_slope < -0.0004 and price < ema_slow)

        if is_calm_tape and long_cross and ema_slope > 0.0001:
            confidence = min(0.9, max(0.5, 0.6 + (median_vol - current_vol) / (median_vol + 1e-6)))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "calm_tape_bullish_trend_continuation",
                    "price": price,
                    "ema_fast": ema_fast,
                    "ema_slope": ema_slope,
                    "current_vol": current_vol,
                    "median_vol": median_vol,
                }
            )

        if is_calm_tape and short_cross and ema_slope < -0.0001:
            confidence = min(0.9, max(0.5, 0.6 + (median_vol - current_vol) / (median_vol + 1e-6)))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "calm_tape_bearish_trend_continuation",
                    "price": price,
                    "ema_fast": ema_fast,
                    "ema_slope": ema_slope,
                    "current_vol": current_vol,
                    "median_vol": median_vol,
                }
            )

        return None