from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_atr = 14
        self.period_ema = 24
        self.last_exit_bar = -999
        self.cooldown_bars = 16
        self.traded_this_cycle_week = -1

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        
        atr = self._atr(highs, lows, closes, self.period_atr)
        ema24 = self._ema(closes, self.period_ema)
        if atr is None or ema24 is None or closes[-1] == 0:
            return None

        # Structural / Volatility Stop based on ATR
        atr_bps = (atr / closes[-1]) * 10000.0
        dynamic_sl_bps = max(300.0, min(550.0, atr_bps * 1.8))
        dynamic_tp_bps = max(500.0, min(900.0, atr_bps * 2.6))

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        iso_year, iso_week, _ = dt.isocalendar()

        # Exit management: Timed structural exit late Monday UTC before Tuesday
        if ctx.has_position():
            # If reached Monday 20:00 UTC or later, or Tuesday, flat to capture seasonal drift
            if (weekday == 0 and hour >= 20) or (weekday == 1):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_session_end_timed_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "close": closes[-1],
                        "atr_bps": atr_bps
                    }
                )
            return None

        # Entry logic: Cooldown & one-trade-per-week gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if self.traded_this_cycle_week == iso_week:
            return None

        # Crisis / Meltdown risk filter
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Seasonality Window: Sunday 18:00 UTC to Monday 04:00 UTC
        is_sunday_evening = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        
        if is_sunday_evening or is_monday_early:
            # Momentum / Price confirmation: Price not in collapse below fast EMA buffer
            # Avoid catching falling knives by requiring price > EMA24 * 0.985
            if closes[-1] > (ema24 * 0.985):
                self.traded_this_cycle_week = iso_week
                confidence = 0.70
                if closes[-1] > ema24:
                    confidence = 0.85

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=dynamic_sl_bps,
                    take_profit_bps=dynamic_tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sunday_late_drift_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "price": closes[-1],
                        "ema24": ema24,
                        "atr_bps": atr_bps,
                        "dynamic_sl_bps": dynamic_sl_bps,
                        "dynamic_tp_bps": dynamic_tp_bps
                    }
                )

        return None