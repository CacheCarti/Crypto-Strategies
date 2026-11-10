from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityReversion(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18 * 3600,
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.cooldown_bars = 6
        self.rsi_period = 14
        self.ema_period = 30

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
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 15)
        if len(closes) < self.ema_period + 1:
            return None

        # Check market stress
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "regime": regime, "crisis_score": crisis_score}
                )
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday is 0, Sunday is 6
        hour = dt.hour

        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        if rsi is None or ema is None:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Monday drift profit capture or Tuesday cutoff exit
            time_exit = (weekday == 0 and hour >= 21) or (weekday == 1 and hour >= 2) or (bars_held >= 22)
            technical_exhaustion = (rsi > 70.0 and bars_held >= 6)

            if time_exit or technical_exhaustion:
                reason = "monday_session_close" if time_exit else "rsi_exhaustion_target"
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": reason,
                        "rsi": rsi,
                        "bars_held": bars_held,
                        "weekday": weekday,
                        "hour": hour
                    }
                )
            return None

        # Entry gating: respect cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Window: Late Sunday (16:00 - 23:00 UTC) into Early Monday (00:00 - 08:00 UTC)
        is_sunday_window = (weekday == 6 and hour >= 16)
        is_monday_morning = (weekday == 0 and hour <= 8)
        if not (is_sunday_window or is_monday_morning):
            return None

        # Price setup: Oversold dip or pullback into support, not in freefall
        funding = ctx.features.get("funding_rate_solusdt", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)

        # We look for washed-out sentiment or mild pullback during the weekend lull
        rsi_valid = 28.0 <= rsi <= 48.0
        funding_neutral_or_negative = funding <= 0.00015
        momentum_not_crashing = sol_ret > -0.15

        if rsi_valid and funding_neutral_or_negative and momentum_not_crashing:
            self.entry_bar = ctx.bar_index
            conf = 0.7 if funding < 0.0 else 0.6
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=18 * 3600,
                metadata={
                    "reason": "sunday_monday_seasonality_dip",
                    "rsi": round(rsi, 2),
                    "ema": round(ema, 2),
                    "funding": round(funding, 6),
                    "weekday": weekday,
                    "hour": hour
                }
            )

        return None