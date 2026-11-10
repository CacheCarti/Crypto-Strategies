from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 64800,  # ~18 hours
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -999
        self.cooldown_bars = 16
        self.rsi_period = 14
        self.trend_period = 50

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()  # Monday is 0, Sunday is 6
        hour = bar_time.hour

        # Hard exit: flatten Monday evening UTC (20:00 to 23:00) or early Tuesday
        if ctx.has_position():
            if (weekday == 0 and hour >= 20) or weekday == 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_tuesday", "hour": hour, "weekday": weekday}
                )
            return None

        # Cooldown guard to enforce ~1-2 selective trades weekly
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Filter: Avoid broad market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Seasonal Entry Window: Sunday late UTC (18:00 - 23:00)
        is_sunday_late = (weekday == 6 and hour >= 18)
        # Fallback window: Early Monday Asia/Europe morning (00:00 - 05:00 UTC)
        is_monday_early = (weekday == 0 and hour <= 5)

        if not (is_sunday_late or is_monday_early):
            return None

        # Indicators
        current_price = ctx.bar.close
        ema_trend = self._ema(closes, self.trend_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if ema_trend is None or rsi_val is None:
            return None

        # Price gate: Prevent buying into parabolic crashes or severe overbought spikes
        # Address prior family failure (drift bleed) by demanding momentum stabilization (RSI > 38 and < 68)
        # and ensure price is holding near/above the medium-term base.
        trend_buffer = ema_trend * 0.985
        if current_price < trend_buffer or rsi_val > 68.0 or rsi_val < 38.0:
            return None

        # Sizing / Confidence logic based on trend strength
        confidence = 0.70 if current_price >= ema_trend else 0.55
        self.last_trade_bar = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_monday_seasonality_long",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi_val, 2),
                "ema50": round(ema_trend, 2),
                "price": round(current_price, 2),
                "crisis_score": round(crisis_score, 2),
            }
        )