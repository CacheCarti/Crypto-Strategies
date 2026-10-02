from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class LowVolTrendPulse(Strategy):
    METADATA = {
        "name": "LowVolTrendPulse",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 21
        self.rv_short_window = 24
        self.rv_long_window = 180
        self.cooldown_bars = 16
        self.last_trade_bar = -999
        self.entry_bar = -999

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _realized_vol(self, closes: List[float], window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(len(closes) - window, len(closes))]
        mean_ret = sum(returns) / len(returns)
        var = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rv_long_window + self.rv_short_window + 10)
        if len(closes) < self.rv_long_window + self.rv_short_window:
            return None

        current_idx = ctx.bar_index
        price = ctx.bar.close

        # Calculate current short-term realized volatility (24-bar window)
        current_rv = self._realized_vol(closes, self.rv_short_window)
        if current_rv is None or current_rv == 0:
            return None

        # Build rolling short-term realized volatility history over 180 bars to compute median
        rv_history = []
        for offset in range(self.rv_long_window):
            sub_end = len(closes) - offset
            sub_start = sub_end - self.rv_short_window - 1
            if sub_start < 0:
                break
            sub_closes = closes[sub_start:sub_end]
            rv = self._realized_vol(sub_closes, self.rv_short_window)
            if rv is not None:
                rv_history.append(rv)

        if len(rv_history) < self.rv_long_window // 2:
            return None

        rv_sorted = sorted(rv_history)
        rv_median = rv_sorted[len(rv_sorted) // 2]

        # Calculate EMA and slope
        ema_now = self._ema(closes, self.fast_ema_period)
        ema_prev = self._ema(closes[:-3], self.fast_ema_period)
        if ema_now is None or ema_prev is None:
            return None

        ema_slope_bps = ((ema_now - ema_prev) / ema_prev) * 10000.0

        is_calm = current_rv < (rv_median * 0.95)
        is_vol_spike = current_rv > (rv_median * 1.40)

        # Position Management & Exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = current_idx - self.entry_bar

            # Exit if volatility spikes violently or EMA trend reverses
            if is_vol_spike:
                self.last_trade_bar = current_idx
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "volatility_spike_exit",
                    "current_rv": current_rv,
                    "rv_median": rv_median,
                    "bars_held": bars_held
                })

            if pos_dir == "long" and (price < ema_now * 0.995 or ema_slope_bps < -2.0):
                self.last_trade_bar = current_idx
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_trend_invalidation",
                    "price": price,
                    "ema": ema_now,
                    "ema_slope_bps": ema_slope_bps
                })

            if pos_dir == "short" and (price > ema_now * 1.005 or ema_slope_bps > 2.0):
                self.last_trade_bar = current_idx
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_trend_invalidation",
                    "price": price,
                    "ema": ema_now,
                    "ema_slope_bps": ema_slope_bps
                })

            return None

        # Entry Logic (Gated by Calm Tape & Cooldown)
        if (current_idx - self.last_trade_bar) < self.cooldown_bars:
            return None

        if not is_calm:
            return None

        # Trend and breakout momentum detection
        prev_close = closes[-2]
        cross_above = prev_close <= ema_now and price > ema_now
        cross_below = prev_close >= ema_now and price < ema_now

        # Long Setup: Calm market + EMA sloping up + Price holding above EMA
        if (cross_above or price > ema_now) and ema_slope_bps > 3.0:
            vol_ratio = current_rv / rv_median
            confidence = min(0.9, max(0.55, 0.65 + (1.0 - vol_ratio) * 0.25))
            self.entry_bar = current_idx
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "calm_regime_bull_trend_continuation",
                    "current_rv": current_rv,
                    "rv_median": rv_median,
                    "ema_slope_bps": ema_slope_bps,
                    "price": price,
                    "ema": ema_now
                }
            )

        # Short Setup: Calm market + EMA sloping down + Price holding below EMA
        if (cross_below or price < ema_now) and ema_slope_bps < -3.0:
            vol_ratio = current_rv / rv_median
            confidence = min(0.9, max(0.55, 0.65 + (1.0 - vol_ratio) * 0.25))
            self.entry_bar = current_idx
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "calm_regime_bear_trend_continuation",
                    "current_rv": current_rv,
                    "rv_median": rv_median,
                    "ema_slope_bps": ema_slope_bps,
                    "price": price,
                    "ema": ema_now
                }
            )

        return None