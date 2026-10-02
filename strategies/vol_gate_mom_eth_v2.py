from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class RegimeGatedMomentum(Strategy):
    METADATA = {
        "name": "RegimeGatedMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.baseline_period = 168
        self.trend_period = 30
        self.cooldown_bars = 16
        self.last_signal_bar = -100
        self.last_exit_bar = -100
        self.vol_history = []

    def _realized_vol(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append((closes[i] - prev) / prev)
        if len(returns) < 2:
            return None
        mean_ret = sum(returns) / len(returns)
        var = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var)

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_period + self.vol_period + 10)
        volumes = ctx.volumes(30)
        if len(closes) < self.baseline_period + self.vol_period:
            return None

        current_vol = self._realized_vol(closes, self.vol_period)
        if current_vol is None or current_vol == 0:
            return None

        self.vol_history.append(current_vol)
        if len(self.vol_history) > self.baseline_period:
            self.vol_history.pop(0)

        if len(self.vol_history) < self.baseline_period:
            return None

        median_vol = statistics.median(self.vol_history)
        if median_vol <= 0:
            return None

        vol_ratio = current_vol / median_vol
        sma_trend = self._sma(closes, self.trend_period)
        if sma_trend is None or sma_trend <= 0:
            return None

        vol_sma = self._sma(volumes, 20)
        current_price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Regime Exit: Significant trend breakdown only to prevent micro-churn
        if has_pos:
            if pos_dir == "long" and current_price < sma_trend * 0.985:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_trend_invalidation",
                        "close": current_price,
                        "sma_trend": round(sma_trend, 2),
                        "vol_ratio": round(vol_ratio, 2)
                    }
                )
            if pos_dir == "short" and current_price > sma_trend * 1.015:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_trend_invalidation",
                        "close": current_price,
                        "sma_trend": round(sma_trend, 2),
                        "vol_ratio": round(vol_ratio, 2)
                    }
                )
            return None

        # Hard multi-bar cooldown after any exit or recent entry
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_signal = ctx.bar_index - self.last_signal_bar
        if bars_since_exit < self.cooldown_bars or bars_since_signal < self.cooldown_bars:
            return None

        # Filter out meltdown / extreme crisis markets
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.80:
            return None

        # Active market gate: realized volatility must be at least 25% above median
        if vol_ratio < 1.25:
            return None

        # Volume confirmation: current volume must exceed average volume
        if vol_sma is not None and ctx.bar.volume < vol_sma * 1.05:
            return None

        confidence = min(0.90, max(0.50, 0.50 + (vol_ratio - 1.25) * 0.40))

        # Long Momentum: Clear breakout above SMA with bullish candle close
        if current_price > sma_trend * 1.008 and ctx.bar.close > ctx.bar.open:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_expansion_bull_breakout",
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "vol_ratio": round(vol_ratio, 2),
                    "sma_trend": round(sma_trend, 2),
                    "price_to_sma": round(current_price / sma_trend, 4),
                    "close": current_price
                }
            )

        # Short Momentum: Clear breakdown below SMA with bearish candle close
        if current_price < sma_trend * 0.992 and ctx.bar.close < ctx.bar.open:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_expansion_bear_breakdown",
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "vol_ratio": round(vol_ratio, 2),
                    "sma_trend": round(sma_trend, 2),
                    "price_to_sma": round(current_price / sma_trend, 4),
                    "close": current_price
                }
            )

        return None