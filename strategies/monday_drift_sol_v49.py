from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar_idx = -1
        self.entered_this_cycle = False
        self.last_cycle_week = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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
        self.entry_bar_idx = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        cal_week = dt.isocalendar()[1]

        if cal_week != self.last_cycle_week:
            self.entered_this_cycle = False
            self.last_cycle_week = cal_week

        ema24 = self._ema(closes, 24)
        ema48 = self._ema(closes, 48)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or ema48 is None or rsi14 is None:
            return None

        # Position management
        if ctx.has_position():
            # Time-based scheduled exit: late Monday before Tuesday open
            if weekday == 0 and hour >= 22:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "monday_close_window", "hour": hour, "rsi": rsi14, "weekday": weekday}
                )
            if weekday == 1 and hour >= 0:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "tuesday_boundary_exit", "hour": hour, "rsi": rsi14, "weekday": weekday}
                )
            # RSI overbought early profit preservation
            if rsi14 > 76.0 and ctx.bar_index - self.entry_bar_idx >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_overbought_drift_exhaustion", "rsi": rsi14, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.entered_this_cycle:
            return None

        # Check market regime: avoid entering during extreme crisis meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Seasonality window: Sunday 18:00 UTC through Monday 08:00 UTC
        in_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 8)
        if not in_entry_window:
            return None

        # Avoid extreme downtrends where weekend dip is a structural crash
        # Require price not to be severely lagging the short EMA (< -3.5%)
        short_lag_pct = (ctx.bar.close - ema24) / ema24
        if short_lag_pct < -0.035:
            return None

        # Relative state filter: avoid buying if already heavily overbought heading into Monday
        if rsi14 > 65.0:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        
        # Trend / momentum state scoring for dynamic confidence
        base_confidence = 0.60
        if ctx.bar.close > ema24:
            base_confidence += 0.10
        if ema24 > ema48:
            base_confidence += 0.10
        if fear_greed < 30:
            base_confidence += 0.05
        confidence = min(0.90, base_confidence)

        self.entered_this_cycle = True
        self.entry_bar_idx = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_late_monday_recovery_drift",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi14, 2),
                "ema24": round(ema24, 2),
                "fear_greed": fear_greed,
                "lag_pct": round(short_lag_pct, 4)
            }
        )