from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 420.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.ema_period = 40
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_price = 0.0
        self.trailing_high = 0.0
        self.entry_bar_idx = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_price = 0.0
        self.trailing_high = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Manage existing long position
        if has_pos and pos_dir == "long":
            self.trailing_high = max(self.trailing_high, current_close)
            bars_held = ctx.bar_index - self.entry_bar_idx

            # Structural volatility trailing check using ATR
            atr_val = self._atr(highs, lows, closes, self.atr_period)
            if atr_val and self.entry_price > 0:
                # If price drops 2.5 * ATR from post-entry high, exit early to protect capital
                trail_sl_dist = 2.5 * atr_val
                if current_close < (self.trailing_high - trail_sl_dist):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "volatility_structure_trailing_stop",
                            "trailing_high": self.trailing_high,
                            "close": current_close,
                            "atr": atr_val,
                            "bars_held": bars_held,
                        },
                    )

            # Planned seasonal time exit: Monday 21:00 UTC through Tuesday
            # Exit before Asian session on Tuesday begins
            is_exit_time = (weekday == 0 and hour >= 21) or (weekday == 1 and hour < 6)
            if is_exit_time:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "scheduled_monday_close_seasonality_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "close": current_close,
                        "bars_held": bars_held,
                    },
                )
            return None

        # Flat state - evaluate entry
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Seasonal Entry Window: Sunday late UTC (18:00 to 23:00)
        is_entry_window = (weekday == 6 and 18 <= hour <= 23)
        if not is_entry_window:
            return None

        # Avoid entering during acute market crises
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Price-based confirmation: SOL must be respecting short-term trend or forming momentum base
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        # Price not severely broken below dynamic baseline (within 1.5% or above EMA40)
        if current_close < ema_val * 0.985:
            return None

        atr_val = self._atr(highs, lows, closes, self.atr_period) or (current_close * 0.02)
        # Adapt stop-loss based on recent ATR bps
        atr_bps = (atr_val / current_close) * 10000.0
        dynamic_sl = max(300.0, min(550.0, atr_bps * 2.2))
        dynamic_tp = max(550.0, dynamic_sl * 1.8)

        self.entry_price = current_close
        self.trailing_high = current_close
        self.entry_bar_idx = ctx.bar_index

        confidence = 0.75 if current_close >= ema_val else 0.65

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=dynamic_sl,
            take_profit_bps=dynamic_tp,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_late_session_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "close": current_close,
                "ema40": ema_val,
                "atr": atr_val,
                "dynamic_sl_bps": dynamic_sl,
                "crisis_score": crisis_score,
            },
        )