from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "Weekend Seasonality ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold over the weekend
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 50
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entered_this_weekend = False

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        closes = ctx.closes(self.warmup + 1)
        if len(closes) < self.warmup:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour

        # Reset the once-per-weekend flag mid-week
        if weekday in (1, 2):
            self.entered_this_weekend = False

        has_pos = ctx.has_position()

        # Exit logic: Exit late Sunday (e.g. Sunday >= 20:00 UTC) or early Monday
        if has_pos:
            is_sunday_night = (weekday == 6 and hour >= 20)
            is_monday = (weekday == 0)
            if is_sunday_night or is_monday:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_target_window_ended",
                        "weekday": weekday,
                        "hour": hour,
                        "price": ctx.bar.close,
                    }
                )
            return None

        # Guard: Check cooldown and single entry per weekend
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if self.entered_this_weekend:
            return None

        # Entry window: Friday 18:00 to 23:00 UTC
        in_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not in_entry_window:
            return None

        # Regime & Macro Filter: Prior evidence shows weekend long drift fails in severe panic/bear regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.4 or regime in ("CRISIS", "MELTDOWN"):
            return None

        fgi = ctx.features.get("fear_greed_index", 50.0)
        if fgi < 20.0:  # Avoid entering during extreme liquidation / capitulation days
            return None

        # Price-based confirmation: Not heavily overbought, and price holding above intermediate trend
        sma30 = self._sma(closes, 30)
        rsi14 = self._rsi(closes, 14)
        if sma30 is None or rsi14 is None:
            return None

        # Avoid buying blow-off tops right before the weekend
        if rsi14 > 72.0:
            return None

        # Price holding near or above medium-term structure (within 1.5% below SMA30 or above it)
        price = ctx.bar.close
        if price < sma30 * 0.985:
            return None

        self.entered_this_weekend = True

        confidence = 0.65
        if price > sma30 and 40.0 <= rsi14 <= 60.0:
            confidence = 0.85

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=450.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_weekend_expansion_entry",
                "weekday": weekday,
                "hour": hour,
                "rsi14": round(rsi14, 2),
                "sma30": round(sma30, 2),
                "price": price,
                "fgi": fgi,
                "crisis_score": round(crisis_score, 2),
            }
        )