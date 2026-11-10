from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.last_exit_bar = -100
        self.cooldown_bars = 16
        self.entry_bar_idx = -1

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
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup + 1)
        if len(closes) < self.warmup:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        price = ctx.bar.close

        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        has_pos = ctx.has_position()

        if has_pos:
            bars_in_trade = ctx.bar_index - self.entry_bar_idx
            is_tuesday = weekday == 1 and hour >= 0
            is_monday_close = weekday == 0 and hour >= 22
            overextended = rsi14 > 75.0

            if is_monday_close or is_tuesday or overextended or bars_in_trade >= 24:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "seasonality_window_closed",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi14": round(rsi14, 2),
                        "bars_in_trade": bars_in_trade,
                        "price": price,
                    },
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        is_sunday_entry = weekday == 6 and hour >= 18
        is_monday_early = weekday == 0 and hour <= 4

        if is_sunday_entry or is_monday_early:
            if price > ema24 * 0.985 and 35.0 <= rsi14 <= 68.0:
                conf = 0.65
                if price > ema24:
                    conf += 0.15
                if rsi14 < 50.0:
                    conf += 0.10
                conf = min(conf, 0.95)

                self.entry_bar_idx = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=350.0,
                    take_profit_bps=550.0,
                    horizon_seconds=86400,
                    metadata={
                        "reason": "sunday_monday_seasonality_long",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi14": round(rsi14, 2),
                        "ema24": round(ema24, 2),
                        "price": price,
                        "crisis_score": crisis_score,
                    },
                )

        return None