from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.weekly_traded_year = -1
        self.weekly_traded_week = -1
        self.entry_price = 0.0

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
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()  # Sunday is 6, Monday is 0, Tuesday is 1
        hour = bar_time.hour
        iso_year, iso_week, _ = bar_time.isocalendar()

        # Handle active position management
        if ctx.has_position():
            # Exit late Monday / early Tuesday before US close
            time_to_exit = (weekday == 0 and hour >= 21) or (weekday == 1 and hour >= 0)
            
            # Dynamic regime emergency exit
            crisis_score = ctx.market.get("crisis_score", 0.0)
            regime = ctx.regime

            if time_to_exit or crisis_score > 0.65 or regime == "crisis":
                reason = "monday_window_expired" if time_to_exit else "regime_risk_exit"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "crisis_score": crisis_score,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Prevent multi-triggering within the same calendar week
        if self.weekly_traded_year == iso_year and self.weekly_traded_week == iso_week:
            return None

        # Seasonality Entry Window: Sunday 18:00 UTC through Monday 04:00 UTC
        is_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 4)
        if not is_entry_window:
            return None

        # Avoid extreme crisis conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.50 or ctx.regime in ("crisis", "meltdown"):
            return None

        # Technical structure: Price must not be in a severe freefall below EMA50
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        if ema20 is None or ema50 is None:
            return None

        # Relative state: Not heavily broken below EMA50 (within 3% or above)
        price = ctx.bar.close
        if price < ema50 * 0.97:
            return None

        # Volatility normalization for dynamic SL/TP
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or price <= 0:
            return None

        atr_bps = (atr / price) * 10000.0
        stop_loss_bps = max(250.0, min(500.0, atr_bps * 2.2))
        take_profit_bps = max(500.0, min(950.0, stop_loss_bps * 1.8))

        # Sentiment tilt
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        confidence = 0.65
        if fear_greed < 25:
            confidence = 0.55
        elif fear_greed > 50:
            confidence = 0.75

        self.weekly_traded_year = iso_year
        self.weekly_traded_week = iso_week
        self.entry_price = price

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=stop_loss_bps,
            take_profit_bps=take_profit_bps,
            horizon_seconds=86400,
            metadata={
                "reason": "sunday_monday_drift_recovery",
                "weekday": weekday,
                "hour": hour,
                "fear_greed": fear_greed,
                "ema20": ema20,
                "ema50": ema50,
                "atr_bps": atr_bps,
                "price": price
            }
        )