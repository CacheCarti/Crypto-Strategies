from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 180000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_price = 0.0
        self.entry_bar = -1
        self.last_exit_bar = -100
        self.atr_at_entry = 0.0

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
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_price = 0.0
        self.atr_at_entry = 0.0
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        highs = ctx.highs(35)
        lows = ctx.lows(35)
        current_price = ctx.bar.close
        timestamp = ctx.bar.timestamp
        weekday = timestamp.weekday()
        hour = timestamp.hour

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0.0:
            return None

        # Position management
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if self.entry_price == 0.0:
                    pos = ctx.position or {}
                    self.entry_price = pos.get("entry_price", current_price)
                    self.atr_at_entry = atr

                bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

                # Dynamic structure stop: exit if broken below 2.5x entry ATR
                loss_dist = self.entry_price - current_price
                structure_sl_dist = 2.5 * self.atr_at_entry if self.atr_at_entry > 0.0 else (0.055 * self.entry_price)
                if loss_dist >= structure_sl_dist:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "structure_vol_stop_breached",
                        "loss_dist": loss_dist,
                        "structure_sl_dist": structure_sl_dist,
                        "price": current_price
                    })

                # Timed weekend exit: Sunday 20:00 - 23:00 UTC, or safety cap at Monday 04:00 UTC
                is_sunday_exit = (weekday == 6 and hour >= 20)
                is_monday_overdue = (weekday == 0 and hour >= 4)
                if is_sunday_exit or is_monday_overdue or bars_held >= 54:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "timed_weekend_completion",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "price": current_price
                    })
            return None

        # Cooldown guard: at least 12 hours after exit
        if ctx.bar_index - self.last_exit_bar < 12:
            return None

        # Crisis / Meltdown risk filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry window: Friday 18:00 to 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Price-action trend confirmation: price above EMA-24 to avoid catching falling knives
        ema24 = self._ema(closes, 24)
        if ema24 is None or current_price < ema24:
            return None

        # Stop-loss and Take-profit anchored to market volatility
        stop_bps = max(350.0, min(650.0, (atr * 2.5 / current_price) * 10000.0))
        tp_bps = max(500.0, min(900.0, stop_bps * 1.5))

        self.entry_price = current_price
        self.entry_bar = ctx.bar_index
        self.atr_at_entry = atr

        return ctx.signal(
            "long",
            confidence=0.85,
            stop_loss_bps=stop_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=180000,
            metadata={
                "reason": "weekend_drift_trend_aligned_entry",
                "weekday": weekday,
                "hour": hour,
                "price": current_price,
                "ema24": ema24,
                "atr": atr,
                "stop_bps": stop_bps
            }
        )