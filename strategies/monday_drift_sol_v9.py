from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolWeekdaySeasonality(Strategy):
    METADATA = {
        "name": "SOL Weekday Seasonality Swing",
        "domain": "sol_usdc",
        "declared_sl_bps": 420.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_week_id = None

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        atr = self._atr(highs, lows, closes, 14)

        if rsi is None or ema_fast is None or ema_slow is None or atr is None:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # 0=Monday, ..., 6=Sunday
        hour = ts.hour
        week_id = f"{ts.year}_{ts.isocalendar()[1]}_sun"

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # In-position management & scheduled calendar exits
        if ctx.has_position():
            # Exit Sunday trade late Monday (after US session peak) or Tuesday early
            if (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 2):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_post_us_session_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "price": ctx.bar.close,
                    }
                )

            # Exit if overbought exhaustion sets in during recovery
            if rsi > 76.0 and ctx.bar.close > ema_fast:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_overbought_exhaustion_exit",
                        "rsi": round(rsi, 2),
                        "price": ctx.bar.close,
                        "ema_fast": round(ema_fast, 2),
                    }
                )

            # Crisis protection exit
            if regime == "MELTDOWN" or crisis_score > 0.85:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "crisis_meltdown_emergency_exit",
                        "regime": regime,
                        "crisis_score": round(crisis_score, 2),
                    }
                )
            return None

        # Entry logic: Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme meltdown environments
        if regime in ("MELTDOWN", "CRISIS") or crisis_score > 0.75:
            return None

        # Primary Setup: Sunday evening recovery accumulation
        # Sunday late UTC (18:00 - 23:00 UTC) when sell-off momentum is subsiding
        is_sunday_entry_window = (weekday == 6 and hour >= 18)
        if is_sunday_entry_window and self.last_entry_week_id != week_id:
            # Price condition: Not severely overbought (RSI < 62) and price near or holding short-term EMA
            if rsi < 62.0 and ctx.bar.close >= (ema_fast * 0.985):
                self.last_entry_week_id = week_id

                confidence = 0.70
                if fear_greed < 40.0:  # Sentiment discount boost
                    confidence += 0.10
                if ema_fast > ema_slow:
                    confidence += 0.05
                confidence = min(0.95, confidence)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sunday_late_seasonality_accumulation",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "fear_greed": fear_greed,
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                        "atr": round(atr, 4),
                    }
                )

        return None