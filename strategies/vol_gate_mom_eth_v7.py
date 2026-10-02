import math
import statistics
from typing import Optional, Dict, Any
from domains.strategy_contract import Strategy, BarContext, Signal

class RegimeVolMomentum(Strategy):
    METADATA = {
        "name": "Regime Gated Vol Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.baseline_period = 168
        self.trend_period = 24
        self.cooldown_bars = 14
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _calc_realized_vol(self, closes: list, end_idx: int) -> Optional[float]:
        if end_idx < self.vol_period:
            return None
        sub_closes = closes[end_idx - self.vol_period:end_idx + 1]
        returns = [(sub_closes[i] - sub_closes[i - 1]) / sub_closes[i - 1] for i in range(1, len(sub_closes))]
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_period + self.vol_period + 10)
        if len(closes) < self.baseline_period + self.vol_period:
            return None

        current_vol = self._calc_realized_vol(closes, len(closes) - 1)
        if current_vol is None or current_vol == 0:
            return None

        # Sample baseline historical volatility across the 168-bar lookback
        vol_history = []
        for offset in range(0, self.baseline_period, 2):
            idx = len(closes) - 1 - offset
            v = self._calc_realized_vol(closes, idx)
            if v is not None:
                vol_history.append(v)

        if len(vol_history) < 30:
            return None

        median_vol = statistics.median(vol_history)
        if median_vol <= 0:
            return None

        vol_ratio = current_vol / median_vol

        # Trend & Momentum indicators
        trend_slice = closes[-self.trend_period:]
        sma_trend = sum(trend_slice) / len(trend_slice)
        prev_trend_slice = closes[-(self.trend_period + 1):-1]
        prev_sma_trend = sum(prev_trend_slice) / len(prev_trend_slice)

        current_close = ctx.bar.close
        prev_close = closes[-2]
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Manage existing positions
        if has_pos:
            bars_in_pos = ctx.bar_index - self.entry_bar
            
            # 1. Exit if volatility completely collapses into quiet stagnation
            if vol_ratio < 0.80 and bars_in_pos >= 4:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "regime_vol_collapse_to_quiet",
                        "vol_ratio": round(vol_ratio, 3),
                        "bars_in_pos": bars_in_pos,
                        "close": current_close
                    }
                )

            # 2. Structural trend breakdown (requiring meaningful buffer to avoid wick chop)
            if pos_dir == "long" and current_close < sma_trend * 0.995:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_sma_breakdown_exit",
                        "sma": round(sma_trend, 2),
                        "close": current_close,
                        "vol_ratio": round(vol_ratio, 3)
                    }
                )
            elif pos_dir == "short" and current_close > sma_trend * 1.005:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_sma_breakout_exit",
                        "sma": round(sma_trend, 2),
                        "close": current_close,
                        "vol_ratio": round(vol_ratio, 3)
                    }
                )
            return None

        # Hard Cooldown gate after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate: Edge only exists in active, expanded volatility regimes
        if vol_ratio < 1.20:
            return None

        # Dynamic confidence based on volatility expansion degree
        confidence = min(0.90, max(0.60, 0.60 + (vol_ratio - 1.20) * 0.20))

        # Channel breakout levels over lookback window (ignoring current bar)
        highs = ctx.highs(self.trend_period + 1)
        lows = ctx.lows(self.trend_period + 1)
        if len(highs) < self.trend_period + 1 or len(lows) < self.trend_period + 1:
            return None

        highest_prev = max(highs[-self.trend_period:-1])
        lowest_prev = min(lows[-self.trend_period:-1])

        # Selective Entry Triggers:
        # Long: Fresh bullish cross with momentum OR clean 24-bar high breakout
        bullish_cross = (prev_close <= prev_sma_trend and current_close > sma_trend * 1.004)
        bullish_breakout = (current_close > highest_prev and current_close > sma_trend * 1.005)

        if bullish_cross or bullish_breakout:
            self.entry_bar = ctx.bar_index
            self.last_exit_bar = ctx.bar_index
            trigger_reason = "bullish_sma_cross_high_vol" if bullish_cross else "bullish_channel_breakout_high_vol"
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": trigger_reason,
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "sma": round(sma_trend, 2),
                    "close": current_close
                }
            )

        # Short: Fresh bearish cross with momentum OR clean 24-bar low breakdown
        bearish_cross = (prev_close >= prev_sma_trend and current_close < sma_trend * 0.996)
        bearish_breakout = (current_close < lowest_prev and current_close < sma_trend * 0.995)

        if bearish_cross or bearish_breakout:
            self.entry_bar = ctx.bar_index
            self.last_exit_bar = ctx.bar_index
            trigger_reason = "bearish_sma_cross_high_vol" if bearish_cross else "bearish_channel_breakout_high_vol"
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": trigger_reason,
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "sma": round(sma_trend, 2),
                    "close": current_close
                }
            )

        return None