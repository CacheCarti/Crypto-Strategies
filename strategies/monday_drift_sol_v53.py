from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 64800,  # ~18 hours hold
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar_idx = -1
        self.trailing_sl_bps = 220.0

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
        self.entry_bar_idx = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # Market regime gating via ctx.market
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Sunday is 6
        hour = ts.hour
        price = ctx.bar.close

        # Manage open position
        if ctx.has_position():
            # Planned seasonal exit: Monday 20:00 UTC through Tuesday 02:00 UTC
            should_exit_seasonal = (weekday == 0 and hour >= 21) or (weekday == 1 and hour >= 0)
            
            # Dynamic time exit guard: if held for over 22 bars
            bars_held = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx > 0 else 0
            
            if should_exit_seasonal or bars_held >= 22:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "seasonal_time_exit", "weekday": weekday, "hour": hour, "bars_held": bars_held}
                )
            return None

        # Check entry cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry window: Sunday 16:00 to 23:00 UTC (weekday == 6) or early Monday 00:00 to 04:00 UTC (weekday == 0)
        valid_seasonal_window = (weekday == 6 and hour >= 16) or (weekday == 0 and hour <= 4)
        if not valid_seasonal_window:
            return None

        # Price-based momentum and trend confirmation to avoid one-off catching falling knives
        ema_fast = self._ema(closes, 12)
        ema_slow = self._ema(closes, 26)
        rsi = self._rsi(closes, 14)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        # Rejection of extreme downtrends: price cannot be deeply below EMA slow, and RSI should show stabilization
        price_above_fast = price >= ema_fast * 0.992
        rsi_rebounding = 34.0 <= rsi <= 64.0
        trend_aligned = ema_fast >= ema_slow * 0.985

        if price_above_fast and rsi_rebounding and trend_aligned:
            self.entry_bar_idx = ctx.bar_index
            confidence = 0.65
            if rsi < 50:
                confidence += 0.10
            if ema_fast > ema_slow:
                confidence += 0.10

            return ctx.signal(
                "long",
                confidence=min(0.90, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "monday_drift_sol_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": round(price, 2)
                }
            )

        return None