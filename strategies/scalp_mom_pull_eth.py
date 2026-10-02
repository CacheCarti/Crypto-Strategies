from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class ScalpPullbackTrend(Strategy):
    METADATA = {
        "name": "Scalp Pullback Trend",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 9
        self.mid_period = 21
        self.slow_period = 50
        self.rsi_period = 14
        self.swing_period = 12
        self.cooldown_bars = 48
        self.last_trade_bar = -999
        self.bars_in_position = 0

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 20)
        highs = ctx.highs(self.slow_period + 20)
        lows = ctx.lows(self.slow_period + 20)

        if len(closes) < self.slow_period + 15:
            return None

        ema9 = self._ema(closes, self.fast_period)
        ema21 = self._ema(closes, self.mid_period)
        ema50 = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema9 is None or ema21 is None or ema50 is None or rsi is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Manage existing position exits
        if has_pos:
            self.bars_in_position += 1

            if pos_dir == "long":
                swing_high = max(highs[-self.swing_period - 1:-1])
                if current_close < ema21:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_trend_break_below_ema21",
                            "close": current_close,
                            "ema21": ema21,
                            "bars_held": self.bars_in_position,
                        },
                    )
                if current_high >= swing_high and current_close > current_open:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_swing_high_target_hit",
                            "close": current_close,
                            "swing_high": swing_high,
                            "bars_held": self.bars_in_position,
                        },
                    )

            elif pos_dir == "short":
                swing_low = min(lows[-self.swing_period - 1:-1])
                if current_close > ema21:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_trend_break_above_ema21",
                            "close": current_close,
                            "ema21": ema21,
                            "bars_held": self.bars_in_position,
                        },
                    )
                if current_low <= swing_low and current_close < current_open:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_swing_low_target_hit",
                            "close": current_close,
                            "swing_low": swing_low,
                            "bars_held": self.bars_in_position,
                        },
                    )

            return None

        # Hard Cooldown Guard: suppress rapid re-entries to control trade count
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Filter adverse volatile regimes
        if ctx.regime in ("volatile", "crisis"):
            return None

        candle_range = max(current_high - current_low, 1e-6)
        lower_wick = min(current_open, current_close) - current_low
        upper_wick = current_high - max(current_open, current_close)

        # Established Trend Thresholds (minimum 10 bps separation between EMA21 and EMA50)
        trend_gap_bps = abs(ema21 - ema50) / ema50 * 10000.0
        min_trend_gap_bps = 12.0

        uptrend = (ema9 > ema21) and (ema21 > ema50) and (trend_gap_bps >= min_trend_gap_bps)
        downtrend = (ema9 < ema21) and (ema21 < ema50) and (trend_gap_bps >= min_trend_gap_bps)

        # High-conviction Pullback Entry:
        # Long: Dips below/at EMA9, rejects with lower wick >= 25% of bar range, closes strong above EMA9, RSI in pullback zone (40-54)
        long_pullback = (
            uptrend
            and current_low <= ema9
            and current_close > ema9
            and current_close > current_open
            and (lower_wick / candle_range) >= 0.25
            and 40.0 <= rsi <= 55.0
        )

        # Short: Rallies above/at EMA9, rejects with upper wick >= 25% of bar range, closes weak below EMA9, RSI in pullback zone (46-60)
        short_pullback = (
            downtrend
            and current_high >= ema9
            and current_close < ema9
            and current_close < current_open
            and (upper_wick / candle_range) >= 0.25
            and 45.0 <= rsi <= 60.0
        )

        if long_pullback:
            self.last_trade_bar = ctx.bar_index
            self.bars_in_position = 0
            conf = min(0.85, 0.60 + (trend_gap_bps / 100.0))

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_ema9_pullback_wick_rejection",
                    "close": current_close,
                    "ema9": ema9,
                    "ema21": ema21,
                    "ema50": ema50,
                    "rsi": rsi,
                    "trend_gap_bps": trend_gap_bps,
                },
            )

        if short_pullback:
            self.last_trade_bar = ctx.bar_index
            self.bars_in_position = 0
            conf = min(0.85, 0.60 + (trend_gap_bps / 100.0))

            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_ema9_pullback_wick_rejection",
                    "close": current_close,
                    "ema9": ema9,
                    "ema21": ema21,
                    "ema50": ema50,
                    "rsi": rsi,
                    "trend_gap_bps": trend_gap_bps,
                },
            )

        return None