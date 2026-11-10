from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_len = 12
        self.ema_slow_len = 36
        self.cooldown_bars = 16
        self.last_exit_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1.0 - k)
        return val

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        # Check existing position for exit logic
        if ctx.has_position():
            # Exit unconditionally late Monday evening (e.g. 21:00 UTC+) or early Tuesday
            if weekday == 0 and hour >= 21:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "monday_session_end", "hour": hour, "weekday": weekday})
            if weekday == 1:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "tuesday_cutoff", "hour": hour, "weekday": weekday})
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: Sunday late UTC (18:00 - 23:00) or very early Monday (00:00 - 04:00)
        is_sunday_entry = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        if not (is_sunday_entry or is_monday_early):
            return None

        # Avoid meltdown/crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        ema_fast = self._ema(closes, self.ema_fast_len)
        ema_slow = self._ema(closes, self.ema_slow_len)
        if ema_fast is None or ema_slow is None:
            return None

        # Session transition confirmation: short-term trend stability
        price = ctx.bar.close
        if price < ema_slow * 0.985:
            return None

        # Cross-asset momentum confirmation: BTC 24h return should not be in severe freefall
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret < -4.0:
            return None

        # Volatility check via ATR
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_pct = (atr / price) * 10000.0

        confidence = 0.65
        if ema_fast > ema_slow:
            confidence += 0.15
        if btc_ret > 0.0:
            confidence += 0.10
        confidence = min(1.0, max(0.1, confidence))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_monday_drift_reversion",
                "weekday": weekday,
                "hour": hour,
                "ema_fast": round(ema_fast, 2),
                "ema_slow": round(ema_slow, 2),
                "btc_ret": round(btc_ret, 2),
                "atr_bps": round(atr_pct, 1),
                "price": round(price, 2),
            }
        )