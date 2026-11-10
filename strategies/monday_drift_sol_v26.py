from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.cooldown_bars = 16
        self.last_traded_week = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        cal_week = dt.isocalendar()[1]

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = float(ctx.market.get("crisis_score", 0.0))
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "risk_off_regime_exit", "regime": regime, "crisis_score": crisis_score}
                )
            return None

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Exit window: Tuesday 00:00 UTC onward, or after Monday US close (>= 22 UTC), or hold limit reached
            if weekday == 1 or (weekday == 0 and hour >= 22) or bars_held >= 26:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "tuesday_seasonality_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "price": ctx.bar.close,
                    }
                )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Maximum one trade execution per calendar week
        if self.last_traded_week == cal_week:
            return None

        # Time gating: Sunday late UTC (18:00 - 23:00) or very early Monday UTC (00:00 - 04:00)
        is_sunday_late = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        if not (is_sunday_late or is_monday_early):
            return None

        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        price = ctx.bar.close

        # Clean momentum and non-exhausted price confirmation:
        # Price should not be in freefall relative to its 24h baseline (>= 98.2% of EMA24),
        # and RSI should reflect a clean reset without deep overbought territory (36 <= RSI <= 66)
        if price >= (ema24 * 0.982) and 36.0 <= rsi14 <= 66.0:
            trend_regime = ctx.market.get("trend_regime", "neutral")
            conf = 0.65
            if trend_regime == "bull":
                conf = 0.8
            elif trend_regime == "bear":
                conf = 0.55

            self.entry_bar = ctx.bar_index
            self.last_traded_week = cal_week

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_into_monday_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi14, 2),
                    "ema24": round(ema24, 2),
                    "price": round(price, 2),
                    "trend_regime": trend_regime,
                }
            )

        return None