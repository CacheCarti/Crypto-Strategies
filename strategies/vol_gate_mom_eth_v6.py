from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class RegimeGatedVolatilityMomentum(Strategy):
    METADATA = {
        "name": "RegimeGatedVolatilityMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_vol_period = 24
        self.vol_history_window = 120
        self.trend_period = 24
        self.cooldown_bars = 3
        self.last_trade_bar = -999

    def _stddev_returns(self, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append((closes[i] - prev) / prev)
        if len(returns) < period:
            return None
        recent_returns = returns[-period:]
        mean_ret = sum(recent_returns) / period
        variance = sum((r - mean_ret) ** 2 for r in recent_returns) / period
        return math.sqrt(variance)

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.vol_history_window + self.fast_vol_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Calculate current realized volatility
        current_vol = self._stddev_returns(closes, self.fast_vol_period)
        if current_vol is None or current_vol <= 0:
            return None

        # Build rolling volatility samples directly from price history for median estimation
        vol_samples = []
        step = 4
        max_lookback = min(len(closes) - self.fast_vol_period - 1, self.vol_history_window)
        for offset in range(0, max_lookback, step):
            sub_closes = closes[:len(closes) - offset]
            v = self._stddev_returns(sub_closes, self.fast_vol_period)
            if v is not None and v > 0:
                vol_samples.append(v)

        if not vol_samples:
            return None

        vol_median = statistics.median(vol_samples)
        if vol_median <= 0:
            return None

        vol_ratio = current_vol / vol_median

        # Trend baseline
        trend_sma = self._sma(closes, self.trend_period)
        if trend_sma is None:
            return None

        curr_close = ctx.bar.close
        prev_close = closes[-2]
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Position exit handling
        if has_pos:
            if pos_dir == "long" and curr_close < trend_sma:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={
                        "reason": "long_trend_break_below_sma",
                        "close": curr_close,
                        "trend_sma": trend_sma,
                        "vol_ratio": round(vol_ratio, 3),
                    },
                )
            elif pos_dir == "short" and curr_close > trend_sma:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={
                        "reason": "short_trend_break_above_sma",
                        "close": curr_close,
                        "trend_sma": trend_sma,
                        "vol_ratio": round(vol_ratio, 3),
                    },
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Active market regime gate: realized vol must be at or above median
        if vol_ratio < 1.0:
            return None

        # Scale confidence with volatility expansion (0.55 to 0.85)
        confidence = min(0.85, 0.55 + max(0.0, (vol_ratio - 1.0) * 0.3))

        # Momentum entry: price crossing or remaining clearly on the trending side
        if curr_close > trend_sma and (prev_close <= trend_sma or curr_close > closes[-2]):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_expansion_bullish_momentum",
                    "close": curr_close,
                    "trend_sma": round(trend_sma, 2),
                    "current_vol": round(current_vol, 6),
                    "vol_median": round(vol_median, 6),
                    "vol_ratio": round(vol_ratio, 3),
                },
            )

        if curr_close < trend_sma and (prev_close >= trend_sma or curr_close < closes[-2]):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_expansion_bearish_momentum",
                    "close": curr_close,
                    "trend_sma": round(trend_sma, 2),
                    "current_vol": round(current_vol, 6),
                    "vol_median": round(vol_median, 6),
                    "vol_ratio": round(vol_ratio, 3),
                },
            )

        return None