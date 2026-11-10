from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityEth(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.last_traded_week = -1
        self.ema_fast_len = 20
        self.ema_slow_len = 50
        self.rsi_len = 14

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        iso_year, iso_week, _ = dt.isocalendar()
        week_key = iso_year * 100 + iso_week

        ema_fast = self._ema(closes, self.ema_fast_len)
        ema_slow = self._ema(closes, self.ema_slow_len)
        rsi = self._rsi(closes, self.rsi_len)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50.0)
        regime = ctx.market.get("regime", "NORMAL")

        if ctx.has_position():
            # Exit late Sunday (20:00 - 23:00 UTC) to capture the weekend drift
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_completed_sunday_close",
                        "price": ctx.bar.close,
                        "rsi": rsi,
                        "hour": hour,
                        "weekday": weekday
                    }
                )

            # Defensive exit early Monday morning if held past Sunday
            if weekday == 0 and hour >= 1:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_open_safety_exit",
                        "price": ctx.bar.close,
                        "rsi": rsi,
                        "hour": hour,
                        "weekday": weekday
                    }
                )

            # Regime breakdown exit
            if regime in ("CRISIS", "MELTDOWN"):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "emergency_regime_exit",
                        "regime": regime,
                        "price": ctx.bar.close
                    }
                )

            return None

        # Entry logic: look for Friday evening entries between 18:00 and 23:00 UTC
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.last_traded_week == week_key:
            return None

        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Friday evening window
        if weekday == 4 and 18 <= hour <= 23:
            # Momentum / trend filters: avoid entering into extreme freefall or overbought spikes
            trend_ok = ctx.bar.close > ema_slow * 0.985
            rsi_ok = 35.0 <= rsi <= 68.0
            fg_ok = fg_index > 20.0

            if trend_ok and rsi_ok and fg_ok:
                self.last_traded_week = week_key
                conf = 0.65
                if ema_fast > ema_slow:
                    conf += 0.15
                if fg_index >= 45.0:
                    conf += 0.10

                return ctx.signal(
                    "long",
                    confidence=min(conf, 1.0),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "weekend_seasonality_friday_entry",
                        "price": ctx.bar.close,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "fg_index": fg_index,
                        "weekday": weekday,
                        "hour": hour
                    }
                )

        return None