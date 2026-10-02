from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TrendPullbackEMA(Strategy):
    METADATA = {
        "name": "TrendPullbackEMA",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 75,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 60
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.min_trend_gap_pct = 0.0035  # Minimum 35 bps spread between EMAs to ensure real trend
        self.last_exit_bar = -999

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit"})
            return None

        closes = ctx.closes(self.slow_period + 15)
        highs = ctx.highs(self.slow_period + 15)
        lows = ctx.lows(self.slow_period + 15)

        if len(closes) < self.slow_period + 5:
            return None

        ema_fast_curr = self._ema(closes, self.fast_period)
        ema_slow_curr = self._ema(closes, self.slow_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        rsi_curr = self._rsi(closes, self.rsi_period)

        if ema_fast_curr is None or ema_slow_curr is None or ema_fast_prev is None or rsi_curr is None:
            return None

        current_close = closes[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]
        pos_dir = ctx.position_direction()

        # Exit management: Require clear break beyond EMA fast to avoid whipsaw churn
        if pos_dir == "long":
            if current_close < ema_fast_curr * 0.997 or rsi_curr > 75.0:
                exit_reason = "ema_fast_breakdown" if current_close < ema_fast_curr * 0.997 else "rsi_overbought_tp"
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "price": current_close,
                        "ema_fast": round(ema_fast_curr, 2),
                        "rsi": round(rsi_curr, 2),
                    },
                )
            return None

        if pos_dir == "short":
            if current_close > ema_fast_curr * 1.003 or rsi_curr < 25.0:
                exit_reason = "ema_fast_breakout" if current_close > ema_fast_curr * 1.003 else "rsi_oversold_tp"
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "price": current_close,
                        "ema_fast": round(ema_fast_curr, 2),
                        "rsi": round(rsi_curr, 2),
                    },
                )
            return None

        # Mandatory cooldown after exits to prevent friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        trend_gap = (ema_fast_curr - ema_slow_curr) / ema_slow_curr

        # High-conviction Long: Strong established uptrend + dip into EMA20 + clean reclaim + healthy RSI
        is_uptrend = trend_gap >= self.min_trend_gap_pct and current_close > ema_slow_curr
        bullish_pullback = (prev_low <= ema_fast_prev) and (current_close > ema_fast_curr)
        bullish_rsi = 45.0 <= rsi_curr <= 62.0

        if is_uptrend and bullish_pullback and bullish_rsi:
            confidence = min(0.85, 0.60 + trend_gap * 8.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "uptrend_ema_pullback_reclaim",
                    "price": current_close,
                    "ema_fast": round(ema_fast_curr, 2),
                    "ema_slow": round(ema_slow_curr, 2),
                    "trend_gap_bps": round(trend_gap * 10000, 1),
                    "rsi": round(rsi_curr, 2),
                },
            )

        # High-conviction Short: Strong established downtrend + bounce into EMA20 + clean rejection + healthy RSI
        is_downtrend = trend_gap <= -self.min_trend_gap_pct and current_close < ema_slow_curr
        bearish_pullback = (prev_high >= ema_fast_prev) and (current_close < ema_fast_curr)
        bearish_rsi = 38.0 <= rsi_curr <= 55.0

        if is_downtrend and bearish_pullback and bearish_rsi:
            confidence = min(0.85, 0.60 + abs(trend_gap) * 8.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "downtrend_ema_pullback_rejection",
                    "price": current_close,
                    "ema_fast": round(ema_fast_curr, 2),
                    "ema_slow": round(ema_slow_curr, 2),
                    "trend_gap_bps": round(abs(trend_gap) * 10000, 1),
                    "rsi": round(rsi_curr, 2),
                },
            )

        return None