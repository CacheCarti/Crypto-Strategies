from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EmaPullbackReclaim(Strategy):
    METADATA = {
        "name": "EMA Pullback Reclaim",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.trend_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 12
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
        closes = ctx.closes(self.trend_period + 10)
        if len(closes) < self.trend_period + 2:
            return None

        ema_fast_curr = self._ema(closes, self.fast_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        ema_trend_curr = self._ema(closes, self.trend_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if ema_fast_curr is None or ema_fast_prev is None or ema_trend_curr is None or rsi_val is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        pos_dir = ctx.position_direction()

        # Position management: exit with modest buffer to avoid micro-whipsaws
        if ctx.has_position():
            if pos_dir == "long" and curr_close < (ema_fast_curr * 0.997):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_ema_cross_under_buffer",
                        "close": curr_close,
                        "ema_fast": ema_fast_curr,
                        "rsi": rsi_val,
                    },
                )
            elif pos_dir == "short" and curr_close > (ema_fast_curr * 1.003):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_ema_cross_over_buffer",
                        "close": curr_close,
                        "ema_fast": ema_fast_curr,
                        "rsi": rsi_val,
                    },
                )
            return None

        # Hard cooldown check after last trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter out high-risk or crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if ctx.regime in ("volatile", "crisis") or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Clean trend definitions with EMA slope separation
        trend_spread = (ema_fast_curr - ema_trend_curr) / ema_trend_curr
        uptrend = (trend_spread > 0.002) and (curr_close > ema_trend_curr)
        downtrend = (trend_spread < -0.002) and (curr_close < ema_trend_curr)

        # Discrete reclaim transitions: previous bar closed strictly beyond EMA, current bar closed back inside trend
        long_reclaim = (prev_close < ema_fast_prev) and (curr_close > ema_fast_curr)
        short_reject = (prev_close > ema_fast_prev) and (curr_close < ema_fast_curr)

        # Long Entry: Strong uptrend + Discrete EMA Reclaim + Neutral-bullish momentum
        if uptrend and long_reclaim and (46.0 <= rsi_val <= 62.0):
            return ctx.signal(
                "long",
                confidence=0.82,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "uptrend_ema20_reclaim_cross",
                    "close": curr_close,
                    "ema_fast": ema_fast_curr,
                    "ema_trend": ema_trend_curr,
                    "rsi": rsi_val,
                    "trend_spread_bps": trend_spread * 10000.0,
                },
            )

        # Short Entry: Strong downtrend + Discrete EMA Rejection + Neutral-bearish momentum
        if downtrend and short_reject and (38.0 <= rsi_val <= 54.0):
            return ctx.signal(
                "short",
                confidence=0.82,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "downtrend_ema20_rejection_cross",
                    "close": curr_close,
                    "ema_fast": ema_fast_curr,
                    "ema_trend": ema_trend_curr,
                    "rsi": rsi_val,
                    "trend_spread_bps": trend_spread * 10000.0,
                },
            )

        return None