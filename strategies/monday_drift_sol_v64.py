from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 50
        self.ema_fast_len = 12
        self.ema_slow_len = 36
        self.atr_len = 14
        self.last_exit_bar = -100
        self.traded_this_window = False

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()
        hour = bar_time.hour
        close = ctx.bar.close

        is_sunday_entry_window = (weekday == 6 and hour >= 18)
        is_monday_entry_window = (weekday == 0 and hour < 10)
        is_allowed_entry_phase = is_sunday_entry_window or is_monday_entry_window

        if weekday not in (6, 0):
            self.traded_this_window = False

        if ctx.has_position():
            if weekday == 0 and hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_close_seasonality_target_reached",
                        "weekday": weekday,
                        "hour": hour,
                        "close": close,
                    }
                )
            if weekday == 1:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "tuesday_hard_seasonal_cutoff",
                        "weekday": weekday,
                        "hour": hour,
                        "close": close,
                    }
                )
            return None

        if not is_allowed_entry_phase:
            return None

        if self.traded_this_window:
            return None

        if (ctx.bar_index - self.last_exit_bar) < 8:
            return None

        ema_fast = self._ema(closes, self.ema_fast_len)
        ema_slow = self._ema(closes, self.ema_slow_len)
        atr = self._atr(highs, lows, closes, self.atr_len)

        if ema_fast is None or ema_slow is None or atr is None or atr <= 0:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret < -4.0:
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr_pct = (atr / close) * 10000.0
        if atr_pct < 35.0:
            return None

        momentum_confirmed = (close > ema_fast) or (ema_fast > ema_slow)
        not_deeply_submerged = close > (ema_slow - 1.5 * atr)

        if momentum_confirmed and not_deeply_submerged:
            self.traded_this_window = True
            
            confidence = 0.65
            if btc_ret > 0:
                confidence += 0.10
            if ema_fast > ema_slow and close > ema_fast:
                confidence += 0.10
            confidence = min(confidence, 0.95)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=750.0,
                horizon_seconds=72000,
                metadata={
                    "reason": "monday_drift_seasonality_momentum_confirm",
                    "weekday": weekday,
                    "hour": hour,
                    "ema_fast": round(ema_fast, 3),
                    "ema_slow": round(ema_slow, 3),
                    "atr": round(atr, 3),
                    "btc_return_pct": round(btc_ret, 3),
                    "crisis_score": round(crisis_score, 3),
                    "close": close,
                }
            )

        return None