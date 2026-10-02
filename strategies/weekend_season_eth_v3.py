from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_period = 30
        self.rsi_period = 14
        self.ema_period = 20
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_period)
        if len(closes) < self.warmup_period:
            return None

        current_close = ctx.bar.close
        timestamp = ctx.bar.timestamp
        weekday = timestamp.weekday()  # Monday=0 ... Sunday=6
        hour = timestamp.hour

        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)

        if rsi is None or ema is None:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        has_pos = ctx.has_position()

        # Exit management
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

            # Scheduled weekend exit: Sunday evening (>= 18:00 UTC) or early Monday
            is_weekend_exit = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 4)
            # Midweek maximum hold exit or technical TP
            is_midweek_exit = (weekday == 4 and hour <= 8) or (bars_held >= 30)
            is_overbought_exit = rsi >= 75.0

            if (is_weekend_exit or is_midweek_exit or is_overbought_exit) and bars_held >= 3:
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "seasonality_exit_or_target",
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held,
                        "weekday": weekday,
                        "hour": hour,
                        "crisis_score": round(crisis_score, 3),
                    }
                )
            return None

        # Cooldown guard after closing a position
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme meltdown crisis only
        if crisis_score > 0.80:
            return None

        # 1. Primary Entry: Weekend Seasonality Drift
        # Opens window starting Friday 12:00 UTC through Saturday 06:00 UTC
        is_weekend_entry_window = (weekday == 4 and hour >= 12) or (weekday == 5 and hour <= 6)
        if is_weekend_entry_window and rsi < 70.0:
            self.entry_bar = ctx.bar_index
            conf = 0.70
            if fear_greed < 45.0:
                conf += 0.10
            if current_close > ema:
                conf += 0.05

            return ctx.signal(
                "long",
                confidence=min(0.90, conf),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "weekend_seasonality_long",
                    "rsi": round(rsi, 2),
                    "ema": round(ema, 2),
                    "fear_greed": fear_greed,
                    "weekday": weekday,
                    "hour": hour,
                }
            )

        # 2. Secondary Entry: Midweek Expansion / Trend Reversal
        # Opens window on Tuesday or Wednesday
        is_midweek_entry_window = (weekday in (1, 2) and 6 <= hour <= 20)
        midweek_setup_ok = (rsi < 62.0) and (current_close > ema * 0.985)

        if is_midweek_entry_window and midweek_setup_ok:
            self.entry_bar = ctx.bar_index
            conf = 0.65
            if rsi < 45.0:
                conf += 0.10

            return ctx.signal(
                "long",
                confidence=min(0.85, conf),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "midweek_trend_continuation_long",
                    "rsi": round(rsi, 2),
                    "ema": round(ema, 2),
                    "fear_greed": fear_greed,
                    "weekday": weekday,
                    "hour": hour,
                }
            )

        return None