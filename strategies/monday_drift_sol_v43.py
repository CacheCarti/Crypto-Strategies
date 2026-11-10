from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 64800,  # ~18 hours hold horizon
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_period = 30
        self.last_exit_bar = -999
        self.cooldown_bars = 16
        self.traded_this_cycle = False
        self.last_seen_day = -1

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_period + 5)
        if len(closes) < self.warmup_period:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday is 0, Sunday is 6
        hour = dt.hour

        # Reset seasonal cycle tracker on Wednesdays (weekday 2)
        if weekday == 2 and self.traded_this_cycle:
            self.traded_this_cycle = False

        price = ctx.bar.close
        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)

        # Active position management: exit rule for Monday seasonality
        if ctx.has_position():
            # Exit window: Monday after 20:00 UTC or anytime on Tuesday/later
            should_seasonal_exit = (weekday == 0 and hour >= 21) or (weekday == 1 and hour >= 0) or (weekday > 1)
            
            # Momentum breakdown safety exit: RSI falls below 38 while losing EMA
            momentum_exit = False
            if rsi14 is not None and ema24 is not None:
                if rsi14 < 38.0 and price < ema24 * 0.985:
                    momentum_exit = True

            if should_seasonal_exit or momentum_exit:
                reason = "monday_session_close" if should_seasonal_exit else "momentum_breakdown"
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi14 or 50.0, 2),
                        "price": round(price, 3),
                    }
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Seasonality Entry Window:
        # Sunday 18:00 UTC through Monday 08:00 UTC (captures weekend bottoming into Monday recovery)
        is_sunday_entry = (weekday == 6 and hour >= 18)
        is_monday_entry = (weekday == 0 and hour <= 8)
        is_entry_window = is_sunday_entry or is_monday_entry

        if not is_entry_window or self.traded_this_cycle:
            return None

        # Technical validation filters:
        # 1. Avoid severe crisis meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # 2. Prevent buying deeply overbought exhaustion: RSI must be between 32 and 66
        if rsi14 is None or not (32.0 <= rsi14 <= 66.0):
            return None

        # 3. Dynamic trend support: Price should not be severely displaced below EMA24
        if ema24 is not None and price < ema24 * 0.965:
            return None

        # Compute signal confidence based on alignment
        conf = 0.60
        if ema24 is not None and price > ema24:
            conf += 0.10
        if rsi14 > 45.0:
            conf += 0.05
        conf = min(0.85, max(0.50, conf))

        self.traded_this_cycle = True

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_late_monday_drift_recovery",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi14, 2),
                "ema24": round(ema24 or price, 3),
                "price": round(price, 3),
                "regime": regime,
            }
        )