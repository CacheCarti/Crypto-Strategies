from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 172800,  # ~48h hold (weekend)
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entry_price = 0.0

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
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour

        # Check existing position for regular exit or early stop
        if ctx.has_position():
            # Exit rule 1: Sunday late evening (UTC 20-23)
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_window_closed",
                        "weekday": weekday,
                        "hour": hour,
                        "price": current_price
                    }
                )
            
            # Exit rule 2: Monday fallback exit if bar was skipped
            if weekday == 0 and hour >= 1:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_overstay_safety_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "price": current_price
                    }
                )

            return None

        # Guard: post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: Friday between 18:00 and 22:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 22)
        if not is_friday_entry_window:
            return None

        # Stricter event boundary with trend confirmation
        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        # Price setup: avoid buying into acute sell-offs or extended extremes
        # Must be holding near or above medium EMA, RSI healthy (38 to 68)
        if current_price < ema24 * 0.985 or rsi14 < 38.0 or rsi14 > 68.0:
            return None

        # Market sentiment filter: avoid extreme market distress
        fg_index = ctx.features.get("fear_greed_index", 50)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if fg_index < 20 or crisis_score > 0.70:
            return None

        # Scaled confidence based on trend alignment and momentum
        confidence = 0.65
        if current_price >= ema24:
            confidence += 0.15
        if 45.0 <= rsi14 <= 58.0:
            confidence += 0.10

        confidence = min(0.95, round(confidence, 2))
        self.entry_price = current_price

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "friday_weekend_inflow_boundary",
                "weekday": weekday,
                "hour": hour,
                "rsi14": round(rsi14, 2),
                "ema24": round(ema24, 2),
                "fear_greed": fg_index,
                "crisis_score": round(crisis_score, 2),
                "price": current_price
            }
        )