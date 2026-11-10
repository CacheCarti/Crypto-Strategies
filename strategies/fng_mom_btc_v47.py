from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 18

    def _ema(self, values, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.ema_period + 5)
        if len(closes) < self.ema_period + 3:
            return None

        # Compute EMA for current and prior bar to detect slope
        ema_now = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-1], self.ema_period)
        if ema_now is None or ema_prev is None:
            return None

        price = ctx.bar.close
        price_prev = closes[-2]
        ema_rising = ema_now > ema_prev
        ema_falling = ema_now < ema_prev

        # Check in-position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()
            
            # Trend exhaustion or counter-trend exit
            exit_signal = False
            exit_reason = ""
            if pos_dir == "long" and (price < ema_now or not ema_rising):
                exit_signal = True
                exit_reason = "ema_loss_or_slope_flatten_long"
            elif pos_dir == "short" and (price > ema_now or not ema_falling):
                exit_signal = True
                exit_reason = "ema_recross_or_slope_flatten_short"
            elif bars_held >= self.max_hold_bars:
                exit_signal = True
                exit_reason = "time_horizon_reached"

            if exit_signal:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "price": price,
                        "ema20": ema_now,
                        "bars_held": bars_held
                    }
                )
            return None

        # Entry logic: enforce post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Sentiment regime gating: stand down at extreme sentiment (fear < 25 or greed > 75)
        fng = ctx.features.get("fear_greed_index", 50.0)
        if fng is None or fng < 25.0 or fng > 75.0:
            return None

        # Crisp entry triggers: require a crossing or breakout over the EMA to avoid firing continuously
        long_cross = (price_prev <= ema_prev) and (price > ema_now) and ema_rising
        short_cross = (price_prev >= ema_prev) and (price < ema_now) and ema_falling

        # Normalized slope filter (ensure meaningful momentum, not flat chop)
        slope_pct = abs(ema_now - ema_prev) / ema_prev

        if long_cross and slope_pct > 0.0003:
            confidence = min(0.85, 0.55 + (fng - 50.0) / 100.0) if fng >= 50 else 0.60
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_ema_cross_up",
                    "price": price,
                    "ema20": ema_now,
                    "fng": fng,
                    "slope_pct": slope_pct
                }
            )

        if short_cross and slope_pct > 0.0003:
            confidence = min(0.85, 0.55 + (50.0 - fng) / 100.0) if fng <= 50 else 0.60
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sentiment_filtered_ema_cross_down",
                    "price": price,
                    "ema20": ema_now,
                    "fng": fng,
                    "slope_pct": slope_pct
                }
            )

        return None