from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 800.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold over the weekend
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entered_this_weekend = False
        self.current_weekend_id = -1

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
        closes = ctx.closes(self.slow_ema_period + 5)
        if len(closes) < self.slow_ema_period:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Friday is 4, Saturday is 5, Sunday is 6
        hour = ts.hour

        # Generate a unique weekend identifier (year and ISO calendar week)
        cal_year, cal_week, _ = ts.isocalendar()
        weekend_id = cal_year * 100 + cal_week

        if weekend_id != self.current_weekend_id:
            self.current_weekend_id = weekend_id
            self.entered_this_weekend = False

        # In-position management
        if ctx.has_position():
            # Exit late Sunday (between 20:00 and 23:00 UTC) to capture weekend drift
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_completed",
                        "weekday": weekday,
                        "hour": hour,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Prevent duplicate entries in the same weekend
        if self.entered_this_weekend:
            return None

        # Seasonality Entry Window: Friday 18:00 to 23:00 UTC
        is_friday_entry = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry:
            return None

        # Market & Trend Regime Gating (Prior evidence: avoid taking weekend risk in heavy bear/meltdown)
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear":
            return None

        # Sentiment gate: avoid extreme panic
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        if fg_index < 20.0:
            return None

        # Price-based trend and momentum confirmation
        ema_fast = self._ema(closes, self.fast_ema_period)
        ema_slow = self._ema(closes, self.slow_ema_period)
        if ema_fast is None or ema_slow is None:
            return None

        # Trend alignment: fast EMA above or near slow EMA, and price not severely breaking down
        if ctx.bar.close < ema_slow * 0.985:
            return None

        # Sizing / Confidence logic based on trend confirmation
        confidence = 0.65
        if ema_fast > ema_slow:
            confidence += 0.15
        if fg_index > 45:
            confidence += 0.10
        confidence = min(1.0, confidence)

        self.entered_this_weekend = True

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
                "price": ctx.bar.close,
                "ema20": ema_fast,
                "ema50": ema_slow,
                "fear_greed": fg_index,
                "trend_regime": trend_regime
            }
        )