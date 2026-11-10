from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        price = ctx.bar.close

        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Exit window: Late Monday UTC (20-23) or Tuesday early, or held over 28 hours
            should_exit = False
            exit_reason = ""

            if weekday == 0 and hour >= 20:
                should_exit = True
                exit_reason = "monday_us_close_exit"
            elif weekday == 1 and hour >= 0:
                should_exit = True
                exit_reason = "tuesday_boundary_exit"
            elif bars_held >= 26:
                should_exit = True
                exit_reason = "max_holding_reached"
            elif rsi14 >= 72.0:
                should_exit = True
                exit_reason = "rsi_overbought_target"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "bars_held": bars_held,
                        "price": price,
                        "rsi": round(rsi14, 2),
                        "weekday": weekday,
                        "hour": hour,
                    },
                )
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry Window: Sunday late UTC (18:00 to 23:00)
        is_sunday_entry_window = (weekday == 6 and 18 <= hour <= 23)

        if not is_sunday_entry_window:
            return None

        # Regime & Macro safety
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 20:
            return None

        # Entry conditions: Not heavily overbought and showing stabilization near/above short trend
        if rsi14 < 62.0 and price >= (ema24 * 0.985):
            self.entry_bar = ctx.bar_index
            confidence = 0.70
            if price > ema24:
                confidence += 0.10
            if fear_greed >= 45:
                confidence += 0.05
            confidence = min(0.95, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=650.0,
                horizon_seconds=86400,
                metadata={
                    "reason": "sunday_night_drift_long",
                    "price": price,
                    "ema24": round(ema24, 2),
                    "rsi14": round(rsi14, 2),
                    "fear_greed": fear_greed,
                    "weekday": weekday,
                    "hour": hour,
                },
            )

        return None