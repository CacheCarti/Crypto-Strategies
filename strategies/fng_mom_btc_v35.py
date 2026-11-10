from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast = 20
        self.ema_slow = 50
        self.slope_period = 3
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_slow + self.slope_period + 5)
        if len(closes) < self.ema_slow + self.slope_period + 2:
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        fng = float(ctx.features.get("fear_greed_index", 50.0))
        # Non-extreme sentiment regime filter: avoid entering during extreme panic or euphoria
        is_neutral_sentiment = 25.0 <= fng <= 75.0

        current_close = closes[-1]
        prev_close = closes[-2]

        ema20_now = self._ema(closes, self.ema_fast)
        ema20_prev = self._ema(closes[:-1], self.ema_fast)
        ema50_now = self._ema(closes, self.ema_slow)

        if ema20_now is None or ema20_prev is None or ema50_now is None:
            return None

        slope20 = (ema20_now - ema20_prev) / ema20_prev
        macro_bull = ema20_now > ema50_now
        macro_bear = ema20_now < ema50_now

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle active position management
        if has_pos:
            # Trailing/trend-break exit or extreme sentiment reversal protection
            if pos_dir == "long":
                if current_close < ema20_now or slope20 < -0.0005 or fng > 85.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_trend_invalidation",
                            "close": current_close,
                            "ema20": ema20_now,
                            "fng": fng,
                        },
                    )
            elif pos_dir == "short":
                if current_close > ema20_now or slope20 > 0.0005 or fng < 15.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_trend_invalidation",
                            "close": current_close,
                            "ema20": ema20_now,
                            "fng": fng,
                        },
                    )
            return None

        # Only evaluate entries if sentiment is within sustainable bounds
        if not is_neutral_sentiment:
            return None

        # Cross and breakout transition triggers to prevent overtrading
        crossed_above_ema20 = prev_close <= ema20_prev and current_close > ema20_now
        crossed_below_ema20 = prev_close >= ema20_prev and current_close < ema20_now

        # Long: Price crosses above rising EMA20 in general uptrend
        if crossed_above_ema20 and slope20 > 0.0002 and macro_bull:
            conf = min(0.85, 0.55 + abs(slope20) * 100.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_momentum_cross_above_ema20",
                    "fng": fng,
                    "ema20": ema20_now,
                    "ema50": ema50_now,
                    "slope20": slope20,
                    "close": current_close,
                },
            )

        # Short: Price crosses below falling EMA20 in general downtrend
        if crossed_below_ema20 and slope20 < -0.0002 and macro_bear:
            conf = min(0.85, 0.55 + abs(slope20) * 100.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_momentum_cross_below_ema20",
                    "fng": fng,
                    "ema20": ema20_now,
                    "ema50": ema50_now,
                    "slope20": slope20,
                    "close": current_close,
                },
            )

        return None