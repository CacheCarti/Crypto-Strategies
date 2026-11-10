from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
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
        self.ema_fast = 20
        self.ema_slow = 50
        self.rsi_period = 14
        self.min_fng = 25.0
        self.max_fng = 75.0
        self.cooldown_bars = 8
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

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
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_slow + 5)
        if len(closes) < self.ema_slow + 5:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2]

        ema20_curr = self._ema(closes, self.ema_fast)
        ema20_prev = self._ema(closes[:-1], self.ema_fast)
        ema50_curr = self._ema(closes, self.ema_slow)
        rsi = self._rsi(closes, self.rsi_period)

        if ema20_curr is None or ema20_prev is None or ema50_curr is None or rsi is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close < ema20_curr and rsi < 48.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_momentum_exhaustion",
                            "close": current_close,
                            "ema20": ema20_curr,
                            "rsi": rsi,
                        },
                    )
            elif pos_dir == "short":
                if current_close > ema20_curr and rsi > 52.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_momentum_exhaustion",
                            "close": current_close,
                            "ema20": ema20_curr,
                            "rsi": rsi,
                        },
                    )
            return None

        # Enforce entry cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Sentiment regime filter: stand down at violent reversal extremes
        fng = float(ctx.features.get("fear_greed_index", 50.0))
        if fng < self.min_fng or fng > self.max_fng:
            return None

        # Directional momentum triggers requiring price breakout crossover through EMA20
        # Long: Price crosses above EMA20, EMA20 is rising, trend confirmed above EMA50, healthy RSI
        long_cross = prev_close <= ema20_prev and current_close > ema20_curr
        ema_rising = ema20_curr > ema20_prev
        trend_bull = ema20_curr > ema50_curr

        if long_cross and ema_rising and trend_bull and (52.0 <= rsi <= 68.0):
            conf = min(0.85, 0.55 + (rsi - 50.0) / 50.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_conditioned_ema_bull_breakout",
                    "fng": fng,
                    "close": current_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "rsi": rsi,
                },
            )

        # Short: Price crosses below EMA20, EMA20 is falling, trend confirmed below EMA50, healthy RSI
        short_cross = prev_close >= ema20_prev and current_close < ema20_curr
        ema_falling = ema20_curr < ema20_prev
        trend_bear = ema20_curr < ema50_curr

        if short_cross and ema_falling and trend_bear and (32.0 <= rsi <= 48.0):
            conf = min(0.85, 0.55 + (50.0 - rsi) / 50.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_conditioned_ema_bear_breakout",
                    "fng": fng,
                    "close": current_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "rsi": rsi,
                },
            )

        return None