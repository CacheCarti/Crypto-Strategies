from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolWeekendSeasonality(Strategy):
    METADATA = {
        "name": "SOL Weekend Seasonality Alpha",
        "domain": "sol_usdc",
        "declared_sl_bps": 420.0,
        "declared_tp_bps": 850.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 12
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.last_entry_week_id = None

    def _rsi(self, closes, period=12):
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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(40)
        lows = ctx.lows(40)

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        atr = self._atr(highs, lows, closes, 14)

        if rsi is None or ema_fast is None or ema_slow is None or atr is None:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        cal = ts.isocalendar()
        week_id = (cal[0], cal[1])

        crisis_score = ctx.market.get("crisis_score", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        has_pos = ctx.has_position()

        # Exit management for active position
        if has_pos:
            # Time-based exit: Monday late US hours (22:00+ UTC) or early Tuesday
            time_exit_due = (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 3)
            # Momentum exhaustion exit
            rsi_overextended = rsi > 78.0
            # Crisis regime protection
            regime_abort = crisis_score > 0.75

            if time_exit_due or rsi_overextended or regime_abort:
                reason = "monday_session_end" if time_exit_due else ("rsi_exhaustion" if rsi_overextended else "crisis_risk_off")
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "price": ctx.bar.close,
                        "crisis_score": round(crisis_score, 2),
                    }
                )
            return None

        # Entry constraints: enforce cooldown and at most 1 seasonal entry cycle per week
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if self.last_entry_week_id == week_id:
            return None

        if crisis_score > 0.65 or fear_greed < 15.0:
            return None

        # Seasonality window: Sunday late UTC (18:00 - 23:00) into Monday early Asia (00:00 - 04:00)
        is_sunday_evening = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)

        if is_sunday_evening or is_monday_early:
            # Price-action filters: Not overbought, and not in severe breakdown
            not_overbought = rsi < 62.0
            price = ctx.bar.close
            trend_support = price > (ema_slow - 1.5 * atr)

            if not_overbought and trend_support:
                confidence = 0.70
                if ema_fast > ema_slow:
                    confidence += 0.15
                if fear_greed >= 40:
                    confidence += 0.05
                confidence = min(0.95, confidence)

                self.last_entry_week_id = week_id
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
                        "rsi": round(rsi, 2),
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                        "atr": round(atr, 2),
                        "fear_greed": fear_greed,
                    }
                )

        return None