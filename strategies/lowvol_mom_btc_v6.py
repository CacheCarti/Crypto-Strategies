from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class CalmRegimeTrendFollower(Strategy):
    METADATA = {
        "name": "Calm Regime Trend Follower",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 26
        self.vol_window = 20
        self.baseline_window = 140
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _calc_vol(self, closes: list, window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        sub = closes[-(window + 1):]
        rets = [(sub[i] - sub[i - 1]) / sub[i - 1] for i in range(1, len(sub))]
        mean_ret = sum(rets) / len(rets)
        variance = sum((r - mean_ret) ** 2 for r in rets) / len(rets)
        return math.sqrt(variance)

    def _vol_regime(self, closes: list) -> tuple:
        total_needed = self.vol_window + self.baseline_window
        if len(closes) < total_needed:
            return None, None
        
        current_vol = self._calc_vol(closes, self.vol_window)
        if current_vol is None:
            return None, None

        # Sample historical realized volatility to compute baseline median
        sampled_vols = []
        for offset in range(0, self.baseline_window, 4):
            idx_end = len(closes) - offset
            sub = closes[idx_end - (self.vol_window + 1):idx_end]
            v = self._calc_vol(sub, self.vol_window)
            if v is not None:
                sampled_vols.append(v)

        if not sampled_vols:
            return None, None

        sampled_vols.sort()
        median_vol = sampled_vols[len(sampled_vols) // 2]
        return current_vol, median_vol

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_window + self.vol_window + 10)
        if len(closes) < self.baseline_window + self.vol_window:
            return None

        current_vol, median_vol = self._vol_regime(closes)
        if current_vol is None or median_vol is None or median_vol == 0:
            return None

        vol_ratio = current_vol / median_vol
        ema_curr = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-3], self.ema_period)
        if ema_curr is None or ema_prev is None or ema_prev == 0:
            return None

        ema_slope = (ema_curr - ema_prev) / ema_prev
        price = ctx.bar.close

        # Position management and exit triggers
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Exit 1: Volatility explosion - stand down in violent tape
            if vol_ratio > 1.65:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "volatility_spike_exit",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "price": price,
                    },
                )

            # Exit 2: Trend invalidation
            if pos_dir == "long" and price < ema_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_trend_broken_long",
                        "price": price,
                        "ema": round(ema_curr, 2),
                        "vol_ratio": round(vol_ratio, 3),
                    },
                )
            elif pos_dir == "short" and price > ema_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_trend_broken_short",
                        "price": price,
                        "ema": round(ema_curr, 2),
                        "vol_ratio": round(vol_ratio, 3),
                    },
                )
            return None

        # Entry logic: Only evaluate if cooldown has expired
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calm market requirement: realized vol must be below long-term median
        is_calm = vol_ratio < 0.92

        if is_calm:
            conf = min(0.85, max(0.55, 0.55 + 0.30 * (1.0 - vol_ratio)))

            # Long entry: Calm tape + price above EMA + positive EMA slope
            if price > ema_curr and ema_slope > 0.0004:
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "calm_regime_ema_bull_trend",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "ema_slope_bps": round(ema_slope * 10000, 2),
                        "price": price,
                        "ema": round(ema_curr, 2),
                    },
                )

            # Short entry: Calm tape + price below EMA + negative EMA slope
            if price < ema_curr and ema_slope < -0.0004:
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "calm_regime_ema_bear_trend",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "ema_slope_bps": round(ema_slope * 10000, 2),
                        "price": price,
                        "ema": round(ema_curr, 2),
                    },
                )

        return None