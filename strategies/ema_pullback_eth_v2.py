from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TrendPullbackReclaim(Strategy):
    METADATA = {
        "name": "TrendPullbackReclaim",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 21
        self.slow_period = 55
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.slow_period + 10)
        highs = ctx.highs(self.slow_period + 10)
        lows = ctx.lows(self.slow_period + 10)

        if len(closes) < self.slow_period + 5:
            return None

        # Filter extreme crisis regimes to prevent friction churn
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        ema_slow_prev = self._ema(closes[:-1], self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or ema_fast_prev is None or ema_slow_prev is None or rsi is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit Logic: Let winners run, exit only when major trend baseline (EMA55) breaks
        if has_pos:
            if pos_dir == "long" and current_close < ema_slow:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_long_trend_break_ema_slow",
                        "price": current_close,
                        "ema_slow": round(ema_slow, 2),
                        "rsi": round(rsi, 2)
                    }
                )
            elif pos_dir == "short" and current_close > ema_slow:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_short_trend_break_ema_slow",
                        "price": current_close,
                        "ema_slow": round(ema_slow, 2),
                        "rsi": round(rsi, 2)
                    }
                )
            return None

        # Multi-bar cooldown guard to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long Setup:
        # 1. Macro trend up (EMA21 clearly above EMA55, EMA55 rising, not bear regime)
        # 2. Strict pullback: previous bar dipped at or below EMA21
        # 3. Reclaim: current bar decisively closes back above EMA21
        # 4. Filtered RSI: pullback zone between 40 and 58
        uptrend = (ema_fast > ema_slow * 1.002) and (ema_slow >= ema_slow_prev) and (trend_regime != "bear")
        long_pullback = prev_low <= ema_fast_prev and prev_close <= ema_fast_prev
        long_reclaim = current_close > ema_fast
        healthy_rsi_long = 40.0 <= rsi <= 58.0

        if uptrend and long_pullback and long_reclaim and healthy_rsi_long:
            conf = min(0.9, max(0.6, 0.6 + (rsi - 40.0) / 45.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_uptrend_pullback_reclaim",
                    "price": current_close,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "rsi": round(rsi, 2),
                    "regime": regime
                }
            )

        # Short Setup:
        # 1. Macro trend down (EMA21 clearly below EMA55, EMA55 falling, not bull regime)
        # 2. Strict pullback: previous bar rallied at or above EMA21
        # 3. Reclaim: current bar decisively closes back below EMA21
        # 4. Filtered RSI: rally pullback zone between 42 and 60
        downtrend = (ema_fast < ema_slow * 0.998) and (ema_slow <= ema_slow_prev) and (trend_regime != "bull")
        short_pullback = prev_high >= ema_fast_prev and prev_close >= ema_fast_prev
        short_reclaim = current_close < ema_fast
        healthy_rsi_short = 42.0 <= rsi <= 60.0

        if downtrend and short_pullback and short_reclaim and healthy_rsi_short:
            conf = min(0.9, max(0.6, 0.6 + (60.0 - rsi) / 45.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_downtrend_pullback_reclaim",
                    "price": current_close,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "rsi": round(rsi, 2),
                    "regime": regime
                }
            )

        return None