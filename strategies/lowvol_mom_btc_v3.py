from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math
import statistics

class CalmTrendMomentum(Strategy):
    METADATA = {
        "name": "Calm Trend Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 24
        self.vol_fast = 20
        self.vol_lookback = 180
        self.cooldown_bars = 7
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _realized_vol(self, closes: List[float], window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - window, len(closes))]
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.vol_lookback + self.vol_fast + 2
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Calculate fast EMA and previous EMA for slope
        ema_now = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-1], self.ema_period)
        if ema_now is None or ema_prev is None:
            return None

        # Calculate rolling realized vol history over vol_lookback
        vol_series = []
        for i in range(self.vol_lookback):
            idx_end = len(closes) - i
            sub_closes = closes[:idx_end]
            v = self._realized_vol(sub_closes, self.vol_fast)
            if v is not None:
                vol_series.append(v)

        if len(vol_series) < 30:
            return None

        current_vol = vol_series[0]
        median_vol = statistics.median(vol_series)

        # Volatility condition: calm tape
        is_calm = current_vol < median_vol
        is_vol_spike = current_vol > (median_vol * 1.7)

        current_price = ctx.bar.close
        prev_price = closes[-2]
        ema_slope = (ema_now - ema_prev) / ema_prev

        # Manage open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Volatility spike exit: tape got erratic, stand down
            if is_vol_spike:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "volatility_spike_exit",
                    "current_vol": current_vol,
                    "median_vol": median_vol,
                    "price": current_price
                })

            # Trend breakdown exit
            if pos_dir == "long" and current_price < ema_now:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_trend_broken",
                    "price": current_price,
                    "ema": ema_now,
                    "ema_slope": ema_slope
                })
            elif pos_dir == "short" and current_price > ema_now:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_trend_broken",
                    "price": current_price,
                    "ema": ema_now,
                    "ema_slope": ema_slope
                })

            return None

        # Gating: Cooldown enforcement
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Only enter during calm tape
        if not is_calm:
            return None

        # Long Setup: Price above EMA, EMA sloping up, fresh cross or strong alignment
        long_condition = (
            current_price > ema_now
            and ema_slope > 0.0003
            and (prev_price <= ema_prev or current_price > closes[-2])
        )

        # Short Setup: Price below EMA, EMA sloping down, fresh cross or strong breakdown
        short_condition = (
            current_price < ema_now
            and ema_slope < -0.0003
            and (prev_price >= ema_prev or current_price < closes[-2])
        )

        vol_ratio = current_vol / max(median_vol, 1e-9)
        confidence = min(0.9, max(0.5, 0.5 + (1.0 - vol_ratio) * 0.4))

        if long_condition:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "calm_regime_bullish_trend_continuation",
                    "price": current_price,
                    "ema": ema_now,
                    "ema_slope": ema_slope,
                    "current_vol": current_vol,
                    "median_vol": median_vol,
                    "vol_ratio": vol_ratio
                }
            )

        if short_condition:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "calm_regime_bearish_trend_continuation",
                    "price": current_price,
                    "ema": ema_now,
                    "ema_slope": ema_slope,
                    "current_vol": current_vol,
                    "median_vol": median_vol,
                    "vol_ratio": vol_ratio
                }
            )

        return None