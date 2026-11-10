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
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -100
        self.cooldown_bars = 20
        self.ema_fast = 12
        self.ema_slow = 36
        self.rsi_period = 14

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()  # Monday=0, Sunday=6
        hour = bar_time.hour
        bar_idx = ctx.bar_index

        rsi_val = self._rsi(closes, self.rsi_period)
        ema_f = self._ema(closes, self.ema_fast)
        ema_s = self._ema(closes, self.ema_slow)

        if rsi_val is None or ema_f is None or ema_s is None:
            return None

        # Position Management & Time-based Exits
        if ctx.has_position():
            # Exit Monday evening / early Tuesday before the seasonal drift decays
            if (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 0):
                self.last_trade_bar = bar_idx
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "seasonal_window_end",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    },
                )
            # Technical safeguard: momentum rolling over heavily while below fast EMA
            if rsi_val > 75.0 and closes[-1] < ema_f:
                self.last_trade_bar = bar_idx
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "overbought_exhaustion",
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard to avoid rapid cycling
        if bar_idx - self.last_trade_bar < self.cooldown_bars:
            return None

        # Macro/Regime filter: Avoid buying during extreme panic or crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry Window: Late Sunday (18:00 to 23:00 UTC)
        # Filters out severe downtrends to fix prior OOS underperformance
        is_sunday_entry_window = (weekday == 6 and hour >= 18)
        trend_support = closes[-1] >= ema_s * 0.985  # not in a free-fall collapse
        not_overextended = 35.0 <= rsi_val <= 65.0   # healthy base without blow-off

        if is_sunday_entry_window and trend_support and not_overextended:
            self.last_trade_bar = bar_idx
            confidence = 0.75 if closes[-1] > ema_f else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_night_drift_setup",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi_val, 2),
                    "ema_fast": round(ema_f, 2),
                    "ema_slow": round(ema_s, 2),
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index