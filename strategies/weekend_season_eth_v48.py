from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold (Friday evening to Sunday night)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 30
        self.rsi_period = 14
        self.last_exit_bar = -100
        self.cooldown_bars = 48  # Hard cooldown: minimum 2 days between trades
        self.entry_bar = -100

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_period + 5)
        if len(closes) < self.trend_period + 2:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday is 0, Friday is 4, Sunday is 6
        hour = dt.hour

        # Calculate lightweight indicators for trend & momentum filter
        trend_sma = self._sma(closes, self.trend_period)
        rsi = self._rsi(closes, self.rsi_period)
        if trend_sma is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Position management / Exit logic
        if ctx.has_position():
            # Scheduled exit: Sunday night between 20:00 and 23:00 UTC
            is_exit_window = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 2)
            
            # Adverse momentum exit: If weekend rally failed and RSI breaks below 35
            failed_rally = (rsi < 35.0) and (ctx.bar_index - self.entry_bar >= 12)

            if is_exit_window or failed_rally:
                reason = "weekend_scheduled_close" if is_exit_window else "adverse_momentum_bail"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "price": round(current_price, 2),
                    }
                )
            return None

        # Entry logic: Cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Target entry window: Friday 18:00 to 23:00 UTC
        is_entry_window = (weekday == 4 and 18 <= hour <= 23)

        if not is_entry_window:
            return None

        # Regime & momentum safety filter:
        # Don't buy in a catastrophic collapse; ensure price is not deep under trend SMA
        # and not severely overbought (RSI < 68)
        not_overbought = rsi < 68.0
        price_near_trend = current_price >= (trend_sma * 0.985)

        if not_overbought and price_near_trend:
            self.entry_bar = ctx.bar_index
            confidence = 0.75 if current_price > trend_sma else 0.60
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "friday_weekend_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "sma": round(trend_sma, 2),
                    "price": round(current_price, 2),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index