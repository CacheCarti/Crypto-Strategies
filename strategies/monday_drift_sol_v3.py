from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolWeekendSeasonality(Strategy):
    METADATA = {
        "name": "SolWeekendSeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.cooldown_bars = 18
        self.last_trade_bar = -100
        self.last_traded_week_year = (-1, -1)

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        cal_year, cal_week, _ = dt.isocalendar()
        current_week_key = (cal_year, cal_week)

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        atr = self._atr(highs, lows, closes, 14)

        if rsi is None or ema_fast is None or ema_slow is None or atr is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # 1. POSITION MANAGEMENT / EXIT LOGIC
        if ctx.has_position():
            # Time-based seasonality exit: Monday late US hours (21:00-23:00 UTC) or Tuesday
            is_monday_exit = (weekday == 0 and hour >= 21)
            is_tuesday_exit = (weekday == 1)

            # Technical breakdown exit: severe RSI crash or regime shift
            is_regime_exit = regime in ("CRISIS", "MELTDOWN") and crisis_score > 0.70

            if is_monday_exit or is_tuesday_exit or is_regime_exit:
                exit_reason = "monday_session_end" if is_monday_exit else ("tuesday_cutoff" if is_tuesday_exit else "crisis_regime_bail")
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "close": round(ctx.bar.close, 4),
                        "crisis_score": round(crisis_score, 3),
                    },
                )
            return None

        # 2. ENTRY LOGIC
        # Gating: Cooldown bars and only one seasonal trade per weekly cycle
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        if self.last_traded_week_year == current_week_key:
            return None

        # Avoid entries in crisis / meltdown regimes
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.60:
            return None

        # Primary Seasonal Entry: Sunday evening UTC (18:00 - 23:00 UTC)
        # Weekday 6 = Sunday
        is_sunday_entry_window = (weekday == 6 and hour >= 18)

        if is_sunday_entry_window:
            # Filter: Do not enter if already heavily overbought or in a massive tailspin
            if 30.0 <= rsi <= 68.0:
                # Dynamic stop-loss and take-profit adaptation via ATR
                atr_bps = (atr / ctx.bar.close) * 10000.0
                dynamic_sl = max(280.0, min(500.0, atr_bps * 1.8))
                dynamic_tp = max(500.0, min(850.0, atr_bps * 3.2))

                confidence = 0.70
                if ema_fast > ema_slow:
                    confidence += 0.10
                if rsi < 50.0:
                    confidence += 0.05

                self.last_trade_bar = ctx.bar_index
                self.last_traded_week_year = current_week_key

                return ctx.signal(
                    "long",
                    confidence=min(0.95, confidence),
                    stop_loss_bps=round(dynamic_sl, 1),
                    take_profit_bps=round(dynamic_tp, 1),
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sunday_late_utc_seasonality_long",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema_fast": round(ema_fast, 4),
                        "ema_slow": round(ema_slow, 4),
                        "atr_bps": round(atr_bps, 1),
                        "regime": regime,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index