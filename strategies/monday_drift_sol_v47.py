from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.entry_bar_idx = -1
        self.consecutive_losses = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar_idx = -1
        ret = position.get("return_bps", 0.0)
        if ret < 0:
            self.consecutive_losses = min(self.consecutive_losses + 1, 3)
        else:
            self.consecutive_losses = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        price = ctx.bar.close

        atr = self._atr(highs, lows, closes, 14)
        ma_trend = self._sma(closes, 30)
        if atr is None or ma_trend is None or price <= 0:
            return None

        has_pos = ctx.has_position()

        # Structural timed / target exits for open position
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx > 0 else 0
            
            # Structural calendar exit: Monday late US session (22:00 UTC) or Tuesday morning
            is_exit_time = (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 2) or (weekday > 1 and weekday < 6)
            if is_exit_time or bars_held >= 30:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "seasonal_window_end_or_max_bars",
                    "weekday": weekday,
                    "hour": hour,
                    "bars_held": bars_held,
                    "price": price
                })

            # Volatility trailing lock: if price moved up more than 1.8 ATR from entry
            pos = ctx.position
            if pos and pos.get("entry_price"):
                entry_px = pos["entry_price"]
                pnl_bps = ((price - entry_px) / entry_px) * 10000.0
                atr_bps = (atr / price) * 10000.0
                if pnl_bps > (atr_bps * 1.8):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "volatility_target_achieved",
                        "pnl_bps": round(pnl_bps, 1),
                        "atr_bps": round(atr_bps, 1),
                        "price": price
                    })
            return None

        # Entry logic: strictly once per week during the seasonal window
        # Cooldown guard: must wait after exiting
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering in crisis or severe meltdown
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        # Seasonal Entry Window: Sunday 18:00 UTC to Monday 04:00 UTC
        is_sunday_evening = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        if not (is_sunday_evening or is_monday_early):
            return None

        # Price-based confirmation: SOL must not be in a freefall beneath 30-bar SMA by more than 2 ATRs
        dist_from_ma = (price - ma_trend) / atr
        if dist_from_ma < -2.0:
            return None

        # Dynamic volatility-based stop loss & take profit
        atr_pct = (atr / price) * 10000.0
        dyn_sl = max(250.0, min(500.0, atr_pct * 1.8))
        dyn_tp = max(350.0, min(750.0, atr_pct * 2.5))

        # Size confidence conservatively after losses
        confidence = max(0.5, 0.85 - (self.consecutive_losses * 0.1))
        self.entry_bar_idx = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=dyn_sl,
            take_profit_bps=dyn_tp,
            horizon_seconds=86400,
            metadata={
                "reason": "sunday_monday_drift_seasonality",
                "weekday": weekday,
                "hour": hour,
                "atr": round(atr, 2),
                "atr_pct_bps": round(atr_pct, 1),
                "dist_from_ma": round(dist_from_ma, 2),
                "consecutive_losses": self.consecutive_losses
            }
        )