from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityEth(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 800.0,
        "declared_hold_seconds": 172800,  # ~48h weekend hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entered_this_weekend = False
        self.last_seen_weekend_id = -1

    def _ema(self, values, period: int) -> Optional[float]:
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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        bar_idx = ctx.bar_index

        # Track unique week identifier to prevent duplicate entries per weekend
        iso_year, iso_week, _ = ts.isocalendar()
        current_weekend_id = iso_year * 100 + iso_week
        if current_weekend_id != self.last_seen_weekend_id:
            self.last_seen_weekend_id = current_weekend_id
            self.entered_this_weekend = False

        has_pos = ctx.has_position()

        # Exit logic: Exit late Sunday (Sunday >= 20:00 UTC) or early Monday
        if has_pos:
            is_exit_window = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 4)
            if is_exit_window:
                self.last_exit_bar = bar_idx
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
            return None

        # Entry logic: Friday between 18:00 and 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        if self.entered_this_weekend:
            return None

        if (bar_idx - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gate: Avoid entering in high crisis or severe meltdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price-based trend filter: Price must not be in a severe downtrend (above EMA 40 or near it)
        ema40 = self._ema(closes, 40)
        if ema40 is None:
            return None

        # Do not enter if price is severely below intermediate trend (> 3.5% below EMA 40)
        current_price = ctx.bar.close
        price_to_ema_ratio = current_price / ema40
        if price_to_ema_ratio < 0.965:
            return None

        # Firing weekend seasonal entry
        self.entered_this_weekend = True
        confidence = 0.75 if price_to_ema_ratio >= 1.0 else 0.60

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
                "price": current_price,
                "ema40": ema40,
                "crisis_score": crisis_score,
                "price_to_ema_ratio": price_to_ema_ratio,
            }
        )