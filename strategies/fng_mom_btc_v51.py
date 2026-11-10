from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentMomentumFilter(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_len = 20
        self.ema_slow_len = 50
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_signal_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard: require spacing between trades and exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_signal_bar) < self.cooldown_bars:
            return None

        # Sentiment regime gating: stand down at extremes (< 25 or > 75)
        fng = ctx.features.get("fear_greed_index", 50.0)
        if fng <= 25.0 or fng >= 75.0:
            return None

        # Market crisis guard: avoid trend continuation during active market panics
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        # Technical Indicators
        ema20_curr = self._ema(closes, self.ema_fast_len)
        ema20_prev = self._ema(closes[:-1], self.ema_fast_len)
        ema50_curr = self._ema(closes, self.ema_slow_len)
        rsi = self._rsi(closes, 14)

        if ema20_curr is None or ema20_prev is None or ema50_curr is None or rsi is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        ema20_rising = ema20_curr > ema20_prev
        ema20_falling = ema20_curr < ema20_prev

        # Long Entry: Clear trend, EMA20 rising above EMA50, price crossing above EMA20, RSI in healthy momentum zone
        long_cross = prev_close <= ema20_prev and curr_close > ema20_curr
        if not ctx.has_position() and long_cross and ema20_rising and (ema20_curr > ema50_curr) and (50.0 < rsi < 68.0):
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_gated_ema20_cross_bullish",
                    "fng": fng,
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "price": round(curr_close, 2),
                },
            )

        # Short Entry: Bearish trend, EMA20 falling below EMA50, price crossing below EMA20, RSI in healthy downward momentum zone
        short_cross = prev_close >= ema20_prev and curr_close < ema20_curr
        if not ctx.has_position() and short_cross and ema20_falling and (ema20_curr < ema50_curr) and (32.0 < rsi < 50.0):
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_gated_ema20_cross_bearish",
                    "fng": fng,
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "price": round(curr_close, 2),
                },
            )

        # Active Position Management: exit if trend breaks structurally or momentum reverses strongly
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (curr_close < ema50_curr or rsi < 42.0):
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_long_trend_break", "rsi": round(rsi, 2), "price": round(curr_close, 2)},
                )
            elif pos_dir == "short" and (curr_close > ema50_curr or rsi > 58.0):
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_short_trend_break", "rsi": round(rsi, 2), "price": round(curr_close, 2)},
                )

        return None