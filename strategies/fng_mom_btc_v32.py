from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.slope_lookback = 3
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 5)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Check cooldown to prevent overtrading and fee drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]

        # Calculate EMA series for current and previous bar to measure slope cleanly
        ema_curr = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-1], self.ema_period)
        if ema_curr is None or ema_prev is None:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Fear & Greed sentiment gate: avoid violent reversal regimes (< 25 or > 75)
        fng = ctx.features.get("fear_greed_index", 50.0)
        sentiment_in_range = 25.0 <= fng <= 75.0
        if not sentiment_in_range:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Check for cross-confirmation trigger rather than continuous state
        crossed_above_ema = prev_close <= ema_prev and current_close > ema_curr
        crossed_below_ema = prev_close >= ema_prev and current_close < ema_curr

        ema_slope_up = ema_curr > ema_prev
        ema_slope_down = ema_curr < ema_prev

        # Long entry trigger: price breaks above rising EMA20 with RSI confirmation
        if crossed_above_ema and ema_slope_up and 50.0 <= rsi <= 68.0:
            if not has_pos or pos_dir != "long":
                return ctx.signal(
                    "long",
                    confidence=0.72,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "ema20_bull_cross_sentiment_guarded",
                        "close": current_close,
                        "ema20": ema_curr,
                        "rsi": rsi,
                        "fng": fng,
                    },
                )

        # Short entry trigger: price breaks below falling EMA20 with RSI confirmation
        if crossed_below_ema and ema_slope_down and 32.0 <= rsi <= 50.0:
            if not has_pos or pos_dir != "short":
                return ctx.signal(
                    "short",
                    confidence=0.72,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "ema20_bear_cross_sentiment_guarded",
                        "close": current_close,
                        "ema20": ema_curr,
                        "rsi": rsi,
                        "fng": fng,
                    },
                )

        # Trend-invalidation exits if position is currently open
        if has_pos:
            if pos_dir == "long" and (current_close < ema_curr or rsi < 42.0):
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "long_trend_invalidation", "close": current_close, "ema20": ema_curr, "rsi": rsi},
                )
            elif pos_dir == "short" and (current_close > ema_curr or rsi > 58.0):
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "short_trend_invalidation", "close": current_close, "ema20": ema_curr, "rsi": rsi},
                )

        return None