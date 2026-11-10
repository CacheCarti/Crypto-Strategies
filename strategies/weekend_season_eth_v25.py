from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold (multi-day swing)
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 24
        self.slow_ema_period = 48
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entered_this_weekend = False

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = dt.hour

        # Reset the weekly trigger flag on Wednesdays
        if weekday == 2 and self.entered_this_weekend:
            self.entered_this_weekend = False

        # If currently holding a position, check for planned exit or regime breakdown
        if ctx.has_position():
            # Scheduled exit: late Sunday UTC (between 20:00 and 23:00)
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_window_closed",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    },
                )
            # Crisis regime early exit
            market_regime = ctx.market.get("regime", "NORMAL")
            if market_regime in ("CRISIS", "MELTDOWN"):
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "crisis_regime_abort",
                        "market_regime": market_regime,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Only evaluate entries on Friday evening (18:00 - 23:00 UTC)
        if weekday != 4 or hour < 18 or self.entered_this_weekend:
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Trend & Momentum indicators to avoid buying falling knives into the weekend
        ema_fast = self._ema(closes, self.fast_ema_period)
        ema_slow = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, 14)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        # Price-based confirmation: fast EMA above slow EMA (mild momentum) and RSI not deeply overbought
        # Ensures drift thesis aligns with prevailing weekly structure without buying parabolic tops
        if ema_fast > ema_slow and 40.0 <= rsi <= 68.0:
            self.entered_this_weekend = True
            
            # Confidence scaled slightly by RSI sweet-spot
            confidence = 0.65 if rsi <= 58.0 else 0.55

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "friday_evening_seasonality_long",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "crisis_score": round(crisis_score, 2),
                },
            )

        return None