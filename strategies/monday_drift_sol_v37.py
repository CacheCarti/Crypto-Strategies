from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 30
        self.atr_period = 14
        self.last_exit_bar = -100
        self.cooldown_bars = 16
        self.current_cycle_id = -1

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 5)
        highs = ctx.highs(self.period + 5)
        lows = ctx.lows(self.period + 5)

        if len(closes) < self.period:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()
        hour = bar_time.hour
        price = ctx.bar.close

        atr = self._atr(highs, lows, closes, self.atr_period)
        ema24 = self._ema(closes, 24)

        if atr is None or ema24 is None or price <= 0:
            return None

        atr_pct = atr / price
        vol_sl_bps = max(250.0, min(650.0, atr_pct * 2.2 * 10000.0))
        vol_tp_bps = max(450.0, min(950.0, atr_pct * 3.8 * 10000.0))

        if ctx.has_position():
            entry_price = ctx.position.get("entry_price", price) if ctx.position else price
            pnl_bps = ((price - entry_price) / entry_price) * 10000.0 if entry_price > 0 else 0.0

            should_time_exit = (weekday == 0 and hour >= 22) or (weekday >= 1 and weekday <= 5)
            structural_stop = pnl_bps <= -vol_sl_bps

            if should_time_exit or structural_stop:
                exit_reason = "monday_window_expired" if should_time_exit else "volatility_stop_hit"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": exit_reason,
                        "weekday": weekday,
                        "hour": hour,
                        "pnl_bps": round(pnl_bps, 2),
                        "price": price,
                        "atr": round(atr, 4),
                    }
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        year_week = bar_time.isocalendar()[1]
        cycle_key = year_week * 10 + (1 if weekday >= 6 else 0)

        in_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 3)

        if in_entry_window and self.current_cycle_id != cycle_key:
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            trend_filter = price >= (ema24 * 0.965)
            if not trend_filter:
                return None

            self.current_cycle_id = cycle_key
            confidence = 0.75 if price >= ema24 else 0.60

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=vol_sl_bps,
                take_profit_bps=vol_tp_bps,
                horizon_seconds=86400,
                metadata={
                    "reason": "sunday_monday_drift_recovery",
                    "weekday": weekday,
                    "hour": hour,
                    "price": price,
                    "ema24": round(ema24, 2),
                    "atr_bps": round(atr_pct * 10000.0, 1),
                    "vol_sl_bps": round(vol_sl_bps, 1),
                    "vol_tp_bps": round(vol_tp_bps, 1),
                }
            )

        return None