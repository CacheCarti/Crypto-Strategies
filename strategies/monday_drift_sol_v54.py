from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 64800,  # ~18 hours hold target
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = self.METADATA["warmup_bars"]
        self.cooldown_bars = 16
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.warmup)
        if len(closes) < self.warmup:
            return None

        bar = ctx.bar
        ts = bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Sunday is 6
        hour = ts.hour

        # Manage open position
        if ctx.has_position():
            # Exit late Monday / pre-Tuesday or early if adverse trend develops
            if (weekday == 0 and hour >= 21) or weekday == 1:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "session_target_reached", "weekday": weekday, "hour": hour, "price": bar.close}
                )
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter crisis or extreme high vol environments
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Seasonality Window: Sunday 18:00 UTC through Monday 04:00 UTC
        is_sunday_late = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        if not (is_sunday_late or is_monday_early):
            return None

        # Cross-asset macro confirmation: BTC not crashing
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret < -3.0:
            return None

        # Price setup: price above EMA20 and not extended above EMA8 (micro pullback within structural trend)
        ema20 = self._ema(closes, 20)
        ema8 = self._ema(closes, 8)
        if ema20 is None or ema8 is None:
            return None

        # Momentum & volatility filter
        highs = ctx.highs(self.warmup)
        lows = ctx.lows(self.warmup)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        atr_pct = (atr / bar.close) * 10000.0  # ATR in bps
        # Skip dead or overly wild bars
        if atr_pct < 40.0 or atr_pct > 350.0:
            return None

        # Entry condition: holding above the short-term trend benchmark
        if bar.close >= ema20:
            confidence = 0.65
            if bar.close > ema8:
                confidence += 0.15
            if btc_ret > 0.5:
                confidence += 0.10

            confidence = min(confidence, 0.95)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "monday_drift_seasonality_momentum",
                    "weekday": weekday,
                    "hour": hour,
                    "price": bar.close,
                    "ema20": ema20,
                    "ema8": ema8,
                    "btc_return_pct": btc_ret,
                    "atr_bps": atr_pct,
                }
            )

        return None