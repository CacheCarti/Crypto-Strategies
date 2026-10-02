from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class CalmRegimeCarry(Strategy):
    METADATA = {
        "name": "Calm Regime Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 190,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 36
        self.vol_lookback = 150
        self.ema_fast_period = 21
        self.ema_slow_period = 55
        self.cooldown_bars = 8
        self.last_exit_bar = -100

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _stddev(self, values: List[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / (n - 1)
        return math.sqrt(var)

    def _realized_vol_series(self, closes: List[float], vol_len: int, total_points: int) -> List[float]:
        # Log returns
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        if len(returns) < vol_len + total_points:
            return []
        
        vols = []
        start_idx = len(returns) - total_points
        for i in range(start_idx, len(returns)):
            window = returns[i - vol_len + 1 : i + 1]
            vols.append(self._stddev(window))
        return vols

    def _median(self, values: List[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        n = len(s)
        mid = n // 2
        if n % 2 == 1:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.vol_lookback + self.vol_period + 5
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime_str = ctx.market.get("regime", "NORMAL")
        current_close = ctx.bar.close

        # Calculate realized volatility history
        vols = self._realized_vol_series(closes, self.vol_period, self.vol_lookback)
        if not vols:
            return None

        current_vol = vols[-1]
        median_vol = self._median(vols)
        vol_ratio = (current_vol / median_vol) if median_vol > 1e-8 else 1.0

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        has_pos = ctx.has_position()

        # Position Management & Exit Logic
        if has_pos:
            is_vol_spike = vol_ratio > 1.25 or regime_str in ("CRISIS", "MELTDOWN")
            is_bear_turn = trend_regime == "bear" or current_close < ema_slow

            if is_vol_spike or is_bear_turn:
                reason = "volatility_spike" if is_vol_spike else "trend_break_bear"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "trend_regime": trend_regime,
                        "market_regime": regime_str,
                        "close": round(current_close, 2),
                    },
                )
            return None

        # Entry Logic (Quiet tape carry)
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Favorable quiet tape conditions:
        # 1. Realized vol is strictly below rolling median (quiet regime)
        # 2. Market regime is not volatile or crisis
        # 3. Macro trend regime is bull or neutral (not bear)
        # 4. Price structure confirmation: EMA fast > EMA slow and price > EMA fast
        quiet_tape = vol_ratio < 0.90 and regime_str not in ("CRISIS", "MELTDOWN", "HIGH_VOL")
        valid_trend = trend_regime in ("bull", "neutral")
        trend_aligned = ema_fast > ema_slow and current_close >= ema_fast

        if quiet_tape and valid_trend and trend_aligned:
            # Scaled confidence based on tape calmness and trend strength
            base_conf = 0.65
            if trend_regime == "bull":
                base_conf += 0.15
            if vol_ratio < 0.75:
                base_conf += 0.10
            confidence = min(0.95, base_conf)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_regime_carry_entry",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "trend_regime": trend_regime,
                    "market_regime": regime_str,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": round(current_close, 2),
                },
            )

        return None