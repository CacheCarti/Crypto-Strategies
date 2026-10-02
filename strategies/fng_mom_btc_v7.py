from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 9
        self.slow_period = 26
        self.min_fgi = 28.0
        self.max_fgi = 72.0
        self.min_slope_bps = 2.5
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)
        if len(closes) < self.slow_period + 5:
            return None

        # Hard cooldown guard after position exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        curr_close = closes[-1]
        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        slow_ema_prev = self._ema(closes[:-1], self.slow_period)
        fast_ema_prev = self._ema(closes[:-1], self.fast_period)

        if fast_ema is None or slow_ema is None or slow_ema_prev is None or fast_ema_prev is None:
            return None

        slow_slope_bps = ((slow_ema - slow_ema_prev) / slow_ema_prev) * 10000.0
        fgi = float(ctx.features.get("fear_greed_index", 50.0))
        atr = self._atr(highs, lows, closes, period=14)
        atr_pct = (atr / curr_close) if atr and curr_close > 0 else 0.01

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if fast_ema < slow_ema or curr_close < slow_ema:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_trend_exhaustion",
                            "close": curr_close,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "fgi": fgi,
                        },
                    )
            elif pos_dir == "short":
                if fast_ema > slow_ema or curr_close > slow_ema:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_trend_exhaustion",
                            "close": curr_close,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "fgi": fgi,
                        },
                    )
            return None

        # Sentiment regime gating: stand down during extreme fear/greed
        if not (self.min_fgi <= fgi <= self.max_fgi):
            return None

        # Strict Crossover Triggers only (removes loose price-cross noise)
        bullish_crossover = fast_ema_prev <= slow_ema_prev and fast_ema > slow_ema
        bearish_crossover = fast_ema_prev >= slow_ema_prev and fast_ema < slow_ema

        # Long Entry: Clean EMA cross with decisive upward slope and price above fast EMA
        if bullish_crossover and slow_slope_bps >= self.min_slope_bps and curr_close > fast_ema:
            confidence = min(0.9, 0.60 + (slow_slope_bps / 15.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "gated_bullish_ema_cross",
                    "close": curr_close,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "slope_bps": slow_slope_bps,
                    "atr_pct": atr_pct,
                    "fgi": fgi,
                },
            )

        # Short Entry: Clean EMA cross with decisive downward slope and price below fast EMA
        if bearish_crossover and slow_slope_bps <= -self.min_slope_bps and curr_close < fast_ema:
            confidence = min(0.9, 0.60 + (abs(slow_slope_bps) / 15.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "gated_bearish_ema_cross",
                    "close": curr_close,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "slope_bps": slow_slope_bps,
                    "atr_pct": atr_pct,
                    "fgi": fgi,
                },
            )

        return None