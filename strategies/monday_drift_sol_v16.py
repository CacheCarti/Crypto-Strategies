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
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -999
        self.cooldown_bars = 20
        self.period_ema = 35
        self.period_rsi = 14

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        ema_val = self._ema(closes, self.period_ema)
        rsi_val = self._rsi(closes, self.period_rsi)
        if ema_val is None or rsi_val is None:
            return None

        price = ctx.bar.close

        if ctx.has_position():
            # Exit late Monday / pre-Tuesday US session close (Monday 20:00 - 23:00 UTC)
            if weekday == 0 and hour >= 20:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "monday_window_closing",
                    "price": price,
                    "rsi": rsi_val,
                    "hour": hour
                })
            # Overextended reversal or weekend drift failing
            if rsi_val >= 75.0:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "rsi_overbought_exit",
                    "price": price,
                    "rsi": rsi_val
                })
            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry window: Sunday 18:00 UTC through early Monday 06:00 UTC
        is_sunday_entry = (weekday == 6 and hour >= 18)
        is_monday_entry = (weekday == 0 and hour <= 6)
        if not (is_sunday_entry or is_monday_entry):
            return None

        # Technical confirmation: avoid catastrophic structural downtrends or extreme tops
        # SOL must be holding above or near its dynamic baseline, not in free-fall, RSI healthy
        is_above_trend = price >= (ema_val * 0.985)
        rsi_valid = 38.0 <= rsi_val <= 65.0

        if is_above_trend and rsi_valid:
            self.last_trade_bar = ctx.bar_index
            confidence = 0.70 if price > ema_val else 0.55
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_monday_seasonality_long",
                    "price": price,
                    "ema": ema_val,
                    "rsi": rsi_val,
                    "weekday": weekday,
                    "hour": hour
                }
            )

        return None