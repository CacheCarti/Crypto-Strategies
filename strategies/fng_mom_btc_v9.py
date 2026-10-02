from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 28
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.min_hold_bars = 4
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _ema_series(self, values, period):
        if len(values) < period:
            return []
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        emas = [ema]
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
            emas.append(ema)
        return emas

    def _rsi_series(self, closes, period=14):
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsis = []
        for i in range(period, len(gains) + 1):
            sub_gains = gains[i - period:i]
            sub_losses = losses[i - period:i]
            avg_gain = sum(sub_gains) / period
            avg_loss = sum(sub_losses) / period
            if avg_loss == 0.0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100.0 - (100.0 / (1.0 + rs)))
        return rsis

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 15)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        emas = self._ema_series(closes, self.ema_period)
        rsis = self._rsi_series(closes, self.rsi_period)

        if len(emas) < 3 or len(rsis) < 2:
            return None

        curr_ema, prev_ema = emas[-1], emas[-2]
        curr_rsi, prev_rsi = rsis[-1], rsis[-2]
        curr_close = closes[-1]

        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        in_cooldown = bars_since_exit < self.cooldown_bars

        # Sentiment regime gating: stand down at sentiment extremes
        sentiment_safe = 28.0 <= fg_index <= 72.0
        sentiment_extreme = fg_index < 20.0 or fg_index > 80.0

        if ctx.has_position():
            pos_dir = ctx.position_direction()

            # Prevent immediate churning: require minimum hold unless extreme sentiment hits
            if bars_since_entry < self.min_hold_bars and not sentiment_extreme:
                return None

            # Distinct trend breakdown exits
            if pos_dir == "long":
                if sentiment_extreme or (curr_close < curr_ema and curr_rsi < 42.0):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "sentiment_extreme_exit" if sentiment_extreme else "confirmed_trend_breakdown_long",
                            "fear_greed": fg_index,
                            "rsi": round(curr_rsi, 2),
                            "close": curr_close,
                            "ema": round(curr_ema, 2)
                        }
                    )
            elif pos_dir == "short":
                if sentiment_extreme or (curr_close > curr_ema and curr_rsi > 58.0):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "sentiment_extreme_exit" if sentiment_extreme else "confirmed_trend_reversal_short",
                            "fear_greed": fg_index,
                            "rsi": round(curr_rsi, 2),
                            "close": curr_close,
                            "ema": round(curr_ema, 2)
                        }
                    )
            return None

        # Stand down during cooldown or when sentiment is unsupportive/extreme
        if in_cooldown or not sentiment_safe:
            return None

        # Meaningful EMA slope threshold to filter flat/choppy noise
        min_slope = curr_ema * 0.00025
        ema_rising_strongly = (curr_ema - prev_ema) > min_slope
        ema_falling_strongly = (prev_ema - curr_ema) > min_slope

        # Long Entry: Clear price leadership over rising EMA with strict RSI transition
        rsi_bullish_thrust = prev_rsi <= 53.0 and curr_rsi > 53.0 and curr_rsi < 66.0
        if curr_close > curr_ema and ema_rising_strongly and rsi_bullish_thrust:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.80,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_confirmed_ema_momentum_long",
                    "fear_greed": fg_index,
                    "rsi": round(curr_rsi, 2),
                    "close": curr_close,
                    "ema": round(curr_ema, 2),
                    "slope_bps": round(((curr_ema - prev_ema) / curr_ema) * 10000, 2)
                }
            )

        # Short Entry: Clear downward momentum under falling EMA with strict RSI transition
        rsi_bearish_break = prev_rsi >= 47.0 and curr_rsi < 47.0 and curr_rsi > 34.0
        if curr_close < curr_ema and ema_falling_strongly and rsi_bearish_break:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.80,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_confirmed_ema_momentum_short",
                    "fear_greed": fg_index,
                    "rsi": round(curr_rsi, 2),
                    "close": curr_close,
                    "ema": round(curr_ema, 2),
                    "slope_bps": round(((prev_ema - curr_ema) / curr_ema) * 10000, 2)
                }
            )

        return None