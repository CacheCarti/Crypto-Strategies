from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.last_traded_day = -1
        self.cooldown_until_bar = 0

    def _stddev(self, values: list) -> float:
        if len(values) < 2:
            return 1.0
        mean = sum(values) / len(values)
        var = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(var) if var > 0 else 1.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 14)
        if len(closes) < self.lookback + 14:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_id = current_time.toordinal()

        # Hard close before session end (at or after 21 UTC) or outside trading window
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_21utc", "hour": hour}
                )
            return None

        # Only open entries at US session open window (13:00 to 14:00 UTC)
        if hour not in (13, 14):
            return None

        # Once per day enforcement and cooldown guard
        if self.last_traded_day == day_id:
            return None
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Avoid extreme crisis / meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic normalized momentum across the last 12 bars
        sub_closes = closes[-self.lookback:]
        price_change = sub_closes[-1] - sub_closes[0]
        stdev = self._stddev(sub_closes)
        z_momentum = price_change / stdev

        # Trend filter: fast EMA vs slow EMA
        k_fast = 2.0 / (6 + 1)
        ema_fast = sum(closes[-6:]) / 6.0
        k_slow = 2.0 / (18 + 1)
        ema_slow = sum(closes[-18:]) / 18.0

        # Sentiment regime
        fg = ctx.features.get("fear_greed_index", 50.0)

        # Robust normalized momentum threshold (>= 1.25 stdev move)
        if z_momentum >= 1.25 and ema_fast > ema_slow and fg > 25:
            self.last_traded_day = day_id
            self.cooldown_until_bar = ctx.bar_index + 12
            confidence = min(0.9, 0.55 + (z_momentum - 1.25) * 0.15)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "z_momentum": round(z_momentum, 2),
                    "hour": hour,
                    "fg": fg,
                }
            )

        if z_momentum <= -1.25 and ema_fast < ema_slow and fg < 75:
            self.last_traded_day = day_id
            self.cooldown_until_bar = ctx.bar_index + 12
            confidence = min(0.9, 0.55 + (abs(z_momentum) - 1.25) * 0.15)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "z_momentum": round(z_momentum, 2),
                    "hour": hour,
                    "fg": fg,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 4)