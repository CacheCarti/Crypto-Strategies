from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math
import statistics

class RegimeGatedVolatilityMomentum(Strategy):
    METADATA = {
        "name": "RegimeGatedVolatilityMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 195,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period: int = 24
        self.vol_period: int = 24
        self.vol_history_period: int = 168
        self.cooldown_bars: int = 12
        self.last_exit_bar: int = -999
        self.min_vol_ratio: float = 1.25
        self.price_buffer_bps: float = 25.0

    def _calc_vol(self, closes: List[float], start_idx: int, length: int) -> float:
        returns = []
        for i in range(start_idx - length + 1, start_idx + 1):
            if i <= 0 or i >= len(closes):
                continue
            prev = closes[i - 1]
            if prev <= 0:
                continue
            returns.append((closes[i] - prev) / prev)

        if len(returns) < 2:
            return 0.0

        mean_ret = sum(returns) / len(returns)
        var = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.vol_history_period + self.vol_period + 10
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]

        trend_slice = closes[-self.trend_period:]
        trend_sma = sum(trend_slice) / self.trend_period

        prev_trend_slice = closes[-self.trend_period - 1:-1]
        prev_trend_sma = sum(prev_trend_slice) / self.trend_period

        curr_vol = self._calc_vol(closes, len(closes) - 1, self.vol_period)

        vol_samples = []
        for idx in range(len(closes) - self.vol_history_period, len(closes), 4):
            v = self._calc_vol(closes, idx, self.vol_period)
            if v > 0.0:
                vol_samples.append(v)

        if not vol_samples:
            return None

        median_vol = statistics.median(vol_samples)
        if median_vol <= 0.0:
            return None

        vol_ratio = curr_vol / median_vol

        # Position Management & Regulated Exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            buffer_val = trend_sma * (self.price_buffer_bps / 10000.0)

            # Exit if regime drops significantly into a dead/choppy state
            if vol_ratio < 0.70:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "volatility_collapsed_flat",
                        "vol_ratio": round(vol_ratio, 3),
                        "curr_vol": round(curr_vol, 5),
                        "median_vol": round(median_vol, 5),
                        "price": current_close,
                    }
                )

            # Trend reversal exits with buffer to prevent churn
            if pos_dir == "long" and current_close < (trend_sma - buffer_val):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "trend_reversal_exit_long",
                        "price": current_close,
                        "sma": round(trend_sma, 2),
                        "vol_ratio": round(vol_ratio, 3),
                    }
                )
            elif pos_dir == "short" and current_close > (trend_sma + buffer_val):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "trend_reversal_exit_short",
                        "price": current_close,
                        "sma": round(trend_sma, 2),
                        "vol_ratio": round(vol_ratio, 3),
                    }
                )

            return None

        # Hard Cooldown Guard
        if ctx.bar_index < self.last_exit_bar + self.cooldown_bars:
            return None

        # Tighter Volatility Expansion Gate (at least 25% above median)
        if vol_ratio < self.min_vol_ratio:
            return None

        # Scaled confidence based on relative volatility expansion
        confidence = min(0.95, max(0.55, 0.55 + (vol_ratio - self.min_vol_ratio) * 0.40))
        buffer_val = trend_sma * (self.price_buffer_bps / 10000.0)

        # High-conviction crossover with threshold clearance
        long_cross = (prev_close <= prev_trend_sma) and (current_close > trend_sma + buffer_val)
        short_cross = (prev_close >= prev_trend_sma) and (current_close < trend_sma - buffer_val)

        if long_cross:
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_gated_momentum_long_cross",
                    "price": current_close,
                    "trend_sma": round(trend_sma, 2),
                    "curr_vol": round(curr_vol, 5),
                    "median_vol": round(median_vol, 5),
                    "vol_ratio": round(vol_ratio, 3),
                }
            )

        if short_cross:
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_gated_momentum_short_cross",
                    "price": current_close,
                    "trend_sma": round(trend_sma, 2),
                    "curr_vol": round(curr_vol, 5),
                    "median_vol": round(median_vol, 5),
                    "vol_ratio": round(vol_ratio, 3),
                }
            )

        return None