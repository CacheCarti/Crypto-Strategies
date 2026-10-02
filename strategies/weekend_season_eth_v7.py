from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class EthWeekendSeasonalDrift(Strategy):
    METADATA = {
        "name": "ETH Weekend Seasonality Drift",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 172800,  # ~48 hour intended hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 34
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entered_day = None

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.entered_day = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Warmup and Cooldown Checks
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        timestamp = ctx.bar.timestamp
        weekday = timestamp.weekday()  # Monday=0, Tuesday=1, ..., Friday=4, Saturday=5, Sunday=6
        hour = timestamp.hour

        ema_val = self._ema(closes, self.ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)
        if ema_val is None or rsi_val is None:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50.0)
        has_pos = ctx.has_position()

        # Exit Logic for open position
        if has_pos:
            # Weekend trade exit: Sunday late night (20:00-23:00 UTC) or early Monday
            is_weekend_exit_time = (weekday == 6 and hour >= 20) or (weekday == 0 and hour <= 4)
            # Midweek trade exit: Thursday late / Friday early
            is_midweek_exit_time = (weekday == 3 and hour >= 20) or (weekday == 4 and hour <= 4)

            # Technical profit take or momentum breakdown exit
            overbought_exit = rsi_val > 72.0
            breakdown_exit = rsi_val < 32.0 and current_price < ema_val * 0.97

            if is_weekend_exit_time or is_midweek_exit_time or overbought_exit or breakdown_exit:
                reason = "calendar_weekend_target" if is_weekend_exit_time else (
                    "calendar_midweek_target" if is_midweek_exit_time else (
                        "rsi_overbought_exit" if overbought_exit else "momentum_breakdown_cut"
                    )
                )
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2),
                        "ema": round(ema_val, 2),
                        "close": round(current_price, 2),
                    },
                )
            return None

        # Entry Logic (No Position)
        # Entry Window 1: Friday Afternoon/Evening UTC (16:00 - 23:00)
        friday_entry_window = (weekday == 4 and 16 <= hour <= 23)
        # Entry Window 2: Tuesday Midday Drift (12:00 - 18:00) for active weekly balance
        tuesday_entry_window = (weekday == 1 and 12 <= hour <= 18)

        # Baseline filters to avoid buying into severe freefalls
        not_crashing = rsi_val >= 36.0 and current_price > (ema_val * 0.965)
        not_extreme_fear = fg_index >= 15.0

        if friday_entry_window and not_crashing and not_extreme_fear:
            # Bullish seasonality drift setup
            confidence = 0.75 if (current_price >= ema_val and rsi_val >= 48.0) else 0.60
            self.entered_day = "friday"
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
                    "rsi": round(rsi_val, 2),
                    "ema": round(ema_val, 2),
                    "close": round(current_price, 2),
                    "fear_greed": fg_index,
                },
            )

        if tuesday_entry_window and not_crashing and not_extreme_fear:
            # Midweek continuation setup if above EMA trend
            if current_price >= ema_val and 45.0 <= rsi_val <= 65.0:
                self.entered_day = "tuesday"
                return ctx.signal(
                    "long",
                    confidence=0.65,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=129600,  # ~36 hours
                    metadata={
                        "reason": "tuesday_midweek_expansion_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2),
                        "ema": round(ema_val, 2),
                        "close": round(current_price, 2),
                        "fear_greed": fg_index,
                    },
                )

        return None