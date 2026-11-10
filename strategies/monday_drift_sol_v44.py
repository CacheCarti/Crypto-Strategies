from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.has_entered_this_cycle = False

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        if weekday == 5:
            self.has_entered_this_cycle = False

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            should_exit = False
            exit_reason = ""

            if weekday == 0 and hour >= 21:
                should_exit = True
                exit_reason = "monday_us_close_window"
            elif weekday >= 1 and weekday < 5:
                should_exit = True
                exit_reason = "post_monday_timeout"
            elif bars_held >= 28:
                should_exit = True
                exit_reason = "max_holding_period_elapsed"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    },
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        is_sunday_entry_window = (weekday == 6 and hour >= 18)
        is_monday_early_window = (weekday == 0 and hour <= 4)
        is_timing_valid = is_sunday_entry_window or is_monday_early_window

        if not is_timing_valid or self.has_entered_this_cycle:
            return None

        ema24 = self._ema(closes, 24)
        if ema24 is None:
            return None

        current_close = ctx.bar.close
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr14 = self._atr(highs, lows, closes, 14)

        trend_ok = current_close > (ema24 * 0.985)

        high_vol_recent = max(highs[-12:])
        low_vol_recent = min(lows[-12:])
        vol_range = high_vol_recent - low_vol_recent
        vol_ok = atr14 is not None and (vol_range <= 4.5 * atr14)

        btc_24h_ret = ctx.features.get("btc_return_pct", 0.0)
        cross_asset_ok = btc_24h_ret >= -3.5

        if trend_ok and vol_ok and cross_asset_ok:
            self.has_entered_this_cycle = True
            self.entry_bar = ctx.bar_index
            confidence = 0.70
            if current_close > ema24:
                confidence += 0.10
            if btc_24h_ret > 0:
                confidence += 0.10
            confidence = min(confidence, 0.95)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=600.0,
                horizon_seconds=72000,
                metadata={
                    "reason": "sunday_late_monday_drift_cross_asset_confirmed",
                    "weekday": weekday,
                    "hour": hour,
                    "ema24": round(ema24, 2),
                    "close": round(current_close, 2),
                    "btc_return_pct": round(btc_24h_ret, 2),
                    "atr14": round(atr14 if atr14 else 0.0, 2),
                },
            )

        return None