from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_price = 0.0
        self.cooldown_bars = 8

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
            diff = closes[i] - closes[i-1]
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
        self.entry_bar = -999
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour

        rsi = self._rsi(closes, 14)
        ema_trend = self._ema(closes, 30)

        # Handle active position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            ret_bps = 0.0
            if self.entry_price > 0:
                ret_bps = ((current_price - self.entry_price) / self.entry_price) * 10000.0

            # Exit Conditions:
            # 1. Tuesday rollover: Seasonality window ends by Tuesday 02:00 UTC
            # 2. Time-stop: maximum hold of 18 bars (18 hours)
            # 3. Dynamic profit target when RSI becomes overbought on Monday afternoon
            # 4. Momentum breakdown: price drops well below EMA trend after 6 bars
            time_exit = (weekday == 1 and hour >= 1) or (bars_held >= 18)
            tp_exit = (rsi is not None and rsi > 72.0 and ret_bps > 150.0)
            breakdown_exit = (ema_trend is not None and current_price < ema_trend * 0.985 and bars_held >= 6)

            if time_exit or tp_exit or breakdown_exit:
                reason = "seasonality_window_closed" if time_exit else ("rsi_overbought_take_profit" if tp_exit else "ema_breakdown_stop")
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": reason,
                    "bars_held": bars_held,
                    "ret_bps": ret_bps,
                    "price": current_price,
                    "rsi": rsi or 50.0
                })
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Seasonality Entry Window:
        # Sunday late (18:00 - 23:00 UTC) OR early Monday Asia open (00:00 - 05:00 UTC)
        is_sunday_late = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 5)
        seasonal_window = is_sunday_late or is_monday_early

        if not seasonal_window:
            return None

        # Price-based technical triggers:
        # Avoid buying heavily extended/overbought tops or severe meltdowns
        if rsi is None or ema_trend is None:
            return None

        # We want SOL to not be overbought (RSI between 32 and 62) and showing stabilization near or above trend EMA
        rsi_valid = 32.0 <= rsi <= 62.0
        trend_valid = current_price >= (ema_trend * 0.975)

        if rsi_valid and trend_valid:
            # Confidence scaled by trend alignment and moderate RSI
            confidence = 0.65
            if current_price > ema_trend:
                confidence += 0.15
            if 40.0 <= rsi <= 55.0:
                confidence += 0.10

            self.entry_bar = ctx.bar_index
            self.entry_price = current_price

            return ctx.signal(
                "long",
                confidence=min(0.95, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "monday_seasonality_mean_reversion_long",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": rsi,
                    "ema_trend": ema_trend,
                    "price": current_price
                }
            )

        return None