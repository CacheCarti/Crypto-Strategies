from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityEth(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.last_trade_bar = -999
        self.cooldown_bars = 24  # Wait at least 24h after exit/entry
        self.entered_this_weekend = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday is 0, Sunday is 6, Friday is 4
        hour = dt.hour

        # Reset the weekend entry latch on Thursday/Friday morning
        if weekday in (3, 4) and hour < 12:
            self.entered_this_weekend = False

        # Manage open position
        if ctx.has_position():
            # Exit late Sunday (Sunday 20:00 - 23:00 UTC) or early Monday
            if (weekday == 6 and hour >= 20) or weekday == 0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_close_target_reached",
                        "weekday": weekday,
                        "hour": hour,
                        "price": ctx.bar.close,
                    }
                )

            # Defensive exit if market switches to extreme crisis
            crisis_score = ctx.market.get("crisis_score", 0.0)
            if crisis_score > 0.75:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "crisis_regime_early_exit",
                        "crisis_score": crisis_score,
                        "price": ctx.bar.close,
                    }
                )
            return None

        # Check entry gating
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        if self.entered_this_weekend:
            return None

        # Target entry window: Friday 16:00 to 23:00 UTC
        if weekday == 4 and (16 <= hour <= 23):
            ema_fast = self._ema(closes, self.fast_ema_period)
            ema_slow = self._ema(closes, self.slow_ema_period)
            if ema_fast is None or ema_slow is None:
                return None

            # Trend & regime alignment: avoid entering during steep selloffs or market breakdowns
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Mild momentum confirmation to avoid catching falling knives
            current_price = ctx.bar.close
            if current_price < ema_slow * 0.96:
                return None

            trend_bullish = ema_fast >= ema_slow
            confidence = 0.75 if trend_bullish else 0.55

            self.entered_this_weekend = True
            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "friday_evening_weekend_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": round(current_price, 2),
                    "regime": regime,
                }
            )

        return None