from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "Weekend Seasonality Drift ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 172800,  # ~48 hours weekend holding period
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 48
        self.last_exit_bar = -999
        self.cooldown_bars = 24
        self.entered_this_weekend = False
        self.last_entry_week = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
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
        closes = ctx.closes(self.period + 5)
        if len(closes) < self.period:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday is 0, Friday is 4, Sunday is 6
        hour = dt.hour
        iso_year, iso_week, _ = dt.isocalendar()

        has_pos = ctx.has_position()
        price = ctx.bar.close

        # Reset weekly entry flag on Monday/Tuesday
        if weekday < 4:
            self.entered_this_weekend = False

        # EXIT RULES
        if has_pos:
            # Scheduled weekend exit: Sunday late night (20:00 - 23:00 UTC) or early Monday
            if (weekday == 6 and hour >= 20) or weekday == 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_close_sunday_utc",
                        "price": price,
                        "weekday": weekday,
                        "hour": hour,
                        "bars_since_exit": ctx.bar_index - self.last_exit_bar
                    }
                )
            return None

        # ENTRY RULES (Only enter on Friday evening UTC: 18:00 - 23:00)
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check entry window: Friday from 18:00 to 23:00 UTC
        if weekday == 4 and 18 <= hour <= 23:
            if not self.entered_this_weekend and self.last_entry_week != iso_week:
                ema_filter = self._ema(closes, 30)
                rsi = self._rsi(closes, 14)

                if ema_filter is None or rsi is None:
                    return None

                # Don't buy in deeply overbought exhaustion or extreme crisis
                regime = ctx.market.get("regime", "NORMAL")
                crisis_score = ctx.market.get("crisis_score", 0.0)
                if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.75:
                    return None

                # Require RSI < 70 to avoid buying local tops into the weekend
                if rsi < 70.0:
                    self.entered_this_weekend = True
                    self.last_entry_week = iso_week
                    trend_score = 0.65 if price >= ema_filter else 0.55

                    return ctx.signal(
                        "long",
                        confidence=trend_score,
                        stop_loss_bps=450.0,
                        take_profit_bps=550.0,
                        horizon_seconds=172800,
                        metadata={
                            "reason": "friday_evening_drift_long",
                            "price": price,
                            "ema30": round(ema_filter, 2),
                            "rsi14": round(rsi, 2),
                            "weekday": weekday,
                            "hour": hour,
                            "iso_week": iso_week,
                            "regime": regime
                        }
                    )

        return None