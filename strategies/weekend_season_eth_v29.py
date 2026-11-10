from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entered_this_weekend = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        closes = ctx.closes(55)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour

        if weekday == 0:
            self.entered_this_weekend = False

        if ctx.has_position():
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "sunday_scheduled_exit", "hour": hour, "weekday": weekday}
                )
            if weekday == 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "monday_hard_close", "hour": hour, "weekday": weekday}
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        is_friday_window = (weekday == 4 and hour >= 16)
        is_saturday_window = (weekday == 5 and hour <= 8)

        if not (is_friday_window or is_saturday_window):
            return None

        if self.entered_this_weekend:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.45:
            return None

        ema50 = self._ema(closes, 50)
        rsi14 = self._rsi(closes, 14)
        if ema50 is None or rsi14 is None:
            return None

        current_price = closes[-1]
        
        # Avoid buying severely overextended rallies or deep freefalls
        if rsi14 > 72.0 or rsi14 < 28.0:
            return None

        # Price relative to medium trend baseline
        price_to_ema = (current_price - ema50) / ema50
        if price_to_ema < -0.045:
            return None

        confidence = 0.65
        if price_to_ema > 0.0:
            confidence += 0.1
        if 40.0 <= rsi14 <= 60.0:
            confidence += 0.1

        self.entered_this_weekend = True

        return ctx.signal(
            "long",
            confidence=min(confidence, 0.95),
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "weekend_seasonality_long",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi14, 2),
                "price_to_ema": round(price_to_ema, 4),
                "regime": regime
            }
        )