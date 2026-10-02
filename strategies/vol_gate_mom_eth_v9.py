from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math
import statistics

class VolRegimeGatedMomentum(Strategy):
    METADATA = {
        "name": "VolRegimeGatedMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.trend_period = 24
        self.vol_period = 24
        self.baseline_period = 168
        self.vol_ratio_threshold = 1.18
        self.cooldown_bars = 8
        self.last_action_bar = -100

    def _realized_vol(self, closes: List[float], period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        recent = closes[-(period + 1):]
        returns = [(recent[i] - recent[i - 1]) / recent[i - 1] for i in range(1, len(recent))]
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def _median_volatility(self, closes: List[float], vol_period: int, lookback: int) -> Optional[float]:
        needed = lookback + vol_period + 1
        if len(closes) < needed:
            return None
        sub_closes = closes[-needed:]
        all_returns = [(sub_closes[i] - sub_closes[i - 1]) / sub_closes[i - 1] for i in range(1, len(sub_closes))]
        
        vol_samples = []
        step = 2
        for i in range(vol_period, len(all_returns) + 1, step):
            window = all_returns[i - vol_period:i]
            mean_ret = sum(window) / vol_period
            var = sum((r - mean_ret) ** 2 for r in window) / vol_period
            vol_samples.append(math.sqrt(var))
            
        if not vol_samples:
            return None
        return statistics.median(vol_samples)

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_action_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.baseline_period + self.vol_period + 10
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        current_vol = self._realized_vol(closes, self.vol_period)
        med_vol = self._median_volatility(closes, self.vol_period, self.baseline_period)
        if current_vol is None or med_vol is None or med_vol <= 0:
            return None

        vol_ratio = current_vol / med_vol
        ema_fast_curr = self._ema(closes, self.fast_period)
        ema_slow_curr = self._ema(closes, self.trend_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        ema_slow_prev = self._ema(closes[:-1], self.trend_period)

        if ema_fast_curr is None or ema_slow_curr is None or ema_fast_prev is None or ema_slow_prev is None:
            return None

        curr_price = closes[-1]

        # Manage existing position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Volatility collapse exit: flat when active regime ends
            if vol_ratio < 0.85:
                self.last_action_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.60,
                    metadata={
                        "reason": "vol_collapsed_below_median",
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(med_vol, 6),
                        "vol_ratio": round(vol_ratio, 3),
                        "price": curr_price
                    }
                )

            # Trend reversal exit
            if pos_dir == "long" and ema_fast_curr < ema_slow_curr:
                self.last_action_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_momentum_reversed",
                        "price": curr_price,
                        "ema_fast": round(ema_fast_curr, 2),
                        "ema_slow": round(ema_slow_curr, 2)
                    }
                )
            elif pos_dir == "short" and ema_fast_curr > ema_slow_curr:
                self.last_action_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_momentum_reversed",
                        "price": curr_price,
                        "ema_fast": round(ema_fast_curr, 2),
                        "ema_slow": round(ema_slow_curr, 2)
                    }
                )
            return None

        # Hard cooldown between trades to eliminate friction churn
        if (ctx.bar_index - self.last_action_bar) < self.cooldown_bars:
            return None

        # Tightened regime filter: enter only when volatility is decisively above baseline
        if vol_ratio < self.vol_ratio_threshold:
            return None

        # Clean crossover trigger (avoids firing on every bar during a trend)
        bull_cross = (ema_fast_prev <= ema_slow_prev) and (ema_fast_curr > ema_slow_curr) and (curr_price > ema_slow_curr)
        bear_cross = (ema_fast_prev >= ema_slow_prev) and (ema_fast_curr < ema_slow_curr) and (curr_price < ema_slow_curr)

        # Confidence scales with excess volatility beyond threshold
        scaled_conf = min(0.85, max(0.55, 0.55 + 0.30 * (vol_ratio - self.vol_ratio_threshold)))

        if bull_cross:
            self.last_action_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=scaled_conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_active_bullish_crossover",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(med_vol, 6),
                    "ema_fast": round(ema_fast_curr, 2),
                    "ema_slow": round(ema_slow_curr, 2),
                    "price": curr_price
                }
            )

        if bear_cross:
            self.last_action_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=scaled_conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_active_bearish_crossover",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(med_vol, 6),
                    "ema_fast": round(ema_fast_curr, 2),
                    "ema_slow": round(ema_slow_curr, 2),
                    "price": curr_price
                }
            )

        return None