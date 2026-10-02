from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.min_fg = 30.0
        self.max_fg = 70.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 3:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))

        ema_fast_curr = self._ema(closes, self.fast_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        ema_slow_curr = self._ema(closes, self.slow_period)
        rsi_curr = self._rsi(closes, self.rsi_period)
        rsi_prev = self._rsi(closes[:-1], self.rsi_period)

        if ema_fast_curr is None or ema_fast_prev is None or ema_slow_curr is None or rsi_curr is None or rsi_prev is None:
            return None

        current_price = ctx.bar.close
        prev_price = closes[-2]

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Position management and exit logic
        if has_pos:
            if pos_dir == "long":
                # Exit long only on a definitive trend breakdown or extreme sentiment peak
                if (current_price < ema_slow_curr) or (rsi_curr < 38.0) or (fg_index > 80.0):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_trend_break_or_extreme_greed",
                            "price": current_price,
                            "ema_slow": ema_slow_curr,
                            "rsi": rsi_curr,
                            "fg_index": fg_index,
                        }
                    )
            elif pos_dir == "short":
                # Exit short only on a definitive trend reversal or extreme fear trough
                if (current_price > ema_slow_curr) or (rsi_curr > 62.0) or (fg_index < 20.0):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_trend_break_or_extreme_fear",
                            "price": current_price,
                            "ema_slow": ema_slow_curr,
                            "rsi": rsi_curr,
                            "fg_index": fg_index,
                        }
                    )
            return None

        # Hard multi-bar cooldown after exit to eliminate overtrading and friction churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Sentiment gate: stand down at sentiment extremes where reversals are violent
        if not (self.min_fg <= fg_index <= self.max_fg):
            return None

        fast_slope = ema_fast_curr - ema_fast_prev

        # Long Entry: Requires strict transition (price crossing above rising EMA fast while in confirmed bull structure)
        long_cross = (prev_price <= ema_fast_prev) and (current_price > ema_fast_curr)
        bull_structure = (ema_fast_curr > ema_slow_curr) and (fast_slope > 0.0)
        rsi_bull_momentum = (52.0 <= rsi_curr <= 66.0) and (rsi_curr > rsi_prev)

        if long_cross and bull_structure and rsi_bull_momentum:
            confidence = min(0.90, max(0.60, 0.65 + (rsi_curr - 50.0) / 80.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "sentiment_filtered_bull_crossover",
                    "price": current_price,
                    "ema_fast": ema_fast_curr,
                    "ema_slow": ema_slow_curr,
                    "fast_slope": fast_slope,
                    "rsi": rsi_curr,
                    "fg_index": fg_index,
                }
            )

        # Short Entry: Requires strict transition (price crossing below falling EMA fast while in confirmed bear structure)
        short_cross = (prev_price >= ema_fast_prev) and (current_price < ema_fast_curr)
        bear_structure = (ema_fast_curr < ema_slow_curr) and (fast_slope < 0.0)
        rsi_bear_momentum = (34.0 <= rsi_curr <= 48.0) and (rsi_curr < rsi_prev)

        if short_cross and bear_structure and rsi_bear_momentum:
            confidence = min(0.90, max(0.60, 0.65 + (50.0 - rsi_curr) / 80.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "sentiment_filtered_bear_crossover",
                    "price": current_price,
                    "ema_fast": ema_fast_curr,
                    "ema_slow": ema_slow_curr,
                    "fast_slope": fast_slope,
                    "rsi": rsi_curr,
                    "fg_index": fg_index,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index