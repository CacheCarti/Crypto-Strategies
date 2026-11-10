from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.cooldown_bars = 14
        self.max_hold_bars = 20

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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Check market stress filter
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "risk_regime_exit", "regime": regime, "crisis_score": crisis_score})
            return None

        rsi = self._rsi(closes, 14)
        ema_fast = self._ema(closes, 8)
        ema_slow = self._ema(closes, 24)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour

        # Manage existing long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dict = ctx.position or {}
            entry_p = pos_dict.get("entry_price", closes[-1])
            unrealized_return_bps = ((closes[-1] - entry_p) / entry_p) * 10000.0 if entry_p > 0 else 0.0

            # Exit criteria:
            # 1. Seasonality end window (Monday 20:00 UTC through Tuesday 02:00 UTC)
            is_exit_window = (weekday == 0 and hour >= 20) or (weekday == 1 and hour <= 2)
            # 2. Maximum holding bars safety check
            is_timeout = bars_held >= self.max_hold_bars
            # 3. Dynamic momentum exhaustion (overbought rollover or trend break)
            is_take_momentum = (rsi > 70.0 and closes[-1] < ema_fast and unrealized_return_bps > 150.0)

            if is_exit_window or is_timeout or is_take_momentum:
                self.last_exit_bar = ctx.bar_index
                reason = "seasonality_window_close" if is_exit_window else ("time_horizon_exit" if is_timeout else "momentum_exhaustion")
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": reason,
                    "bars_held": bars_held,
                    "rsi": rsi,
                    "unrealized_return_bps": unrealized_return_bps,
                    "close": closes[-1]
                })
            return None

        # Cooldown guard to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry logic: Sunday late (18-23 UTC) or early Monday pre-drift (0-6 UTC)
        is_sunday_late = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 6)
        valid_timing = is_sunday_late or is_monday_early

        if not valid_timing:
            return None

        # Price-based momentum filter: not heavily overbought, short-term momentum intact or turning up
        price_filter = (rsi < 62.0) and (closes[-1] >= ema_fast * 0.995 or ema_fast > ema_slow)

        if price_filter:
            self.entry_bar = ctx.bar_index
            confidence = 0.80 if ema_fast > ema_slow else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_monday_seasonality_drift",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": closes[-1]
                }
            )

        return None