from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold over the weekend
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.traded_this_weekend = False

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 5)
        if len(closes) < self.slow_ema_period:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour

        # Reset the per-weekend guard during midweek
        if weekday in (1, 2, 3):
            self.traded_this_weekend = False

        # Active position management: exit late Sunday (>= 20:00 UTC) or Monday morning
        if ctx.has_position():
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "sunday_scheduled_exit", "hour": hour, "weekday": weekday}
                )
            if weekday == 0 and hour >= 0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "monday_overstay_exit", "hour": hour, "weekday": weekday}
                )
            return None

        # Cooldown & single-entry per weekend enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if self.traded_this_weekend:
            return None

        # Seasonality window: Friday 18:00 to 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Regime & Macro Gate based on prior failure analysis (avoid severe bear/meltdown drag)
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed < 20.0:
            return None  # Skip under extreme capitulation / panic dump regimes

        # Price confirmation: trend alignment or short-term momentum not collapsing
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        if fast_ema is None or slow_ema is None:
            return None

        # Edge is strongest when market is not in an active severe downtrend
        current_close = ctx.bar.close
        if current_close < slow_ema * 0.96:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        confidence = 0.65
        if trend_regime == "bull" or fast_ema >= slow_ema:
            confidence = 0.85

        self.traded_this_weekend = True
        self.entry_bar = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "friday_weekend_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "fast_ema": round(fast_ema, 2),
                "slow_ema": round(slow_ema, 2),
                "price": round(current_close, 2),
                "fear_greed": round(fear_greed, 1),
                "regime": regime
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index