from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 64800,  # ~18 hours target hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.entry_bar_idx = None
        self.traded_this_week = -1

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
        self.entry_bar_idx = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage open position
        if ctx.has_position():
            # If position direction is long, manage seasonal time-based exit
            dt = ctx.bar.timestamp
            weekday = dt.weekday()
            hour = dt.hour
            bars_held = (ctx.bar_index - self.entry_bar_idx) if self.entry_bar_idx is not None else 18

            # Exit Monday late afternoon / early evening UTC (US active session maturity) or if held >= 24 bars
            is_monday_late = (weekday == 0 and hour >= 20)
            is_tuesday = (weekday >= 1)
            time_exit = is_monday_late or is_tuesday or (bars_held >= 24)

            # Technical momentum exhaust exit: RSI overbought while in profit
            rsi_val = self._rsi(closes, 14) or 50.0
            momentum_exit = rsi_val > 76.0

            if time_exit or momentum_exit:
                exit_reason = "seasonality_session_end" if time_exit else "rsi_overbought_take_profit"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": exit_reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2),
                        "bars_held": bars_held,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        # Window: Late Sunday UTC (18:00 - 23:00) into very early Monday UTC (00:00 - 05:00)
        is_sunday_late = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 5)
        is_entry_window = is_sunday_late or is_monday_early

        if not is_entry_window:
            return None

        # Restrict to at most one trade cycle per calendar week
        current_week = dt.isocalendar()[1]
        if self.traded_this_week == current_week:
            return None

        # Market filters
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.45 or fear_greed < 15.0:
            return None  # Avoid opening long during structural liquidation meltdowns

        # Technical drift validation: not in extreme freefall, price reclaiming baseline
        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema20 is None or rsi is None:
            return None

        # Require reasonable oversold-to-neutral stabilization (RSI between 32 and 60)
        # and price within reasonable distance of short-term EMA (avoid chasing runaway or catching a falling knife)
        drift_valid = (32.0 <= rsi <= 60.0) and (ctx.bar.close >= closes[-2])

        if drift_valid:
            self.traded_this_week = current_week
            self.entry_bar_idx = ctx.bar_index
            confidence = 0.75 if fear_greed >= 35.0 else 0.60

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_monday_drift_recovery",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20, 2),
                    "price": ctx.bar.close,
                    "fear_greed": fear_greed,
                    "crisis_score": crisis_score
                }
            )

        return None