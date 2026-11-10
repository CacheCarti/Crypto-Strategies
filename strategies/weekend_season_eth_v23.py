from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold over the weekend
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_ema_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.traded_this_weekend = False
        self.last_seen_week = -1

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
        closes = ctx.closes(self.trend_ema_period + 10)
        if len(closes) < self.trend_ema_period + 1:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Friday is 4, Sunday is 6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        # Reset weekend traded tracker when a new week begins
        if iso_week != self.last_seen_week:
            self.traded_this_weekend = False
            self.last_seen_week = iso_week

        ema_val = self._ema(closes, self.trend_ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)
        if ema_val is None or rsi_val is None:
            return None

        # Check existing position for exit conditions
        if ctx.has_position():
            # Scheduled exit: Sunday night (20:00 UTC or later) or early Monday
            if (weekday == 6 and hour >= 20) or weekday == 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_target_window_ended",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                    },
                )
            # Momentum breakdown early cut: severe momentum breakdown while holding
            if rsi_val < 32.0 and ctx.bar.close < ema_val * 0.975:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "weekend_momentum_breakdown_cut",
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                        "ema": round(ema_val, 2),
                    },
                )
            return None

        # Entry logic: No current position
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.traded_this_weekend:
            return None

        # Primary Calendar Edge: Friday evening UTC (17:00 to 23:00)
        is_friday_entry_window = (weekday == 4 and 17 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Regime & Technical Quality Filter to avoid falling-knife / broad meltdown entries
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Require reasonable trend posture or local mean-reversion base (not extreme overbought or severe breakdown)
        is_trend_support = ctx.bar.close >= ema_val * 0.985
        not_overbought = rsi_val < 68.0
        not_collapsed = rsi_val > 36.0

        if is_trend_support and not_overbought and not_collapsed:
            self.traded_this_weekend = True
            confidence = 0.65
            if ctx.bar.close > ema_val and rsi_val > 50.0:
                confidence = 0.85

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "friday_evening_weekend_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi_val, 2),
                    "ema": round(ema_val, 2),
                    "close": ctx.bar.close,
                    "regime": market_regime,
                },
            )

        return None