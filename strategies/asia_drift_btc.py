from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class AsiaDriftUsContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.current_date = None
        self.asia_open_price = None
        self.asia_close_price = None
        self.traded_today = False
        self.cooldown_until_bar = 0
        self.min_drift_bps = 25.0
        self.min_confidence = 0.55

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        current_day = (ts.year, ts.month, ts.day)
        hour = ts.hour
        current_bar = ctx.bar_index

        # Reset daily state on a new calendar day
        if self.current_date != current_day:
            self.current_date = current_day
            self.asia_open_price = ctx.bar.open if hour == 0 else None
            self.asia_close_price = None
            self.traded_today = False

        # Capture Asia session boundaries (UTC 0 to 8)
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        if hour == 8 and self.asia_close_price is None:
            self.asia_close_price = ctx.bar.close

        # Fallback if bar 0 was missed but we are in Asia hours
        if hour < 8 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Session Exit: Close positions before the next Asia session begins (UTC 22-23)
        if ctx.has_position() and hour >= 22:
            return ctx.signal(
                "flat",
                confidence=0.75,
                metadata={
                    "reason": "session_close_pre_asia",
                    "exit_hour": hour,
                    "close_price": ctx.bar.close,
                    "bar_index": current_bar,
                },
            )

        # Skip entry if in cooldown, already traded today, or already in position
        if ctx.has_position() or self.traded_today or current_bar < self.cooldown_until_bar:
            return None

        # Entry Window: US session open (UTC 13 to 15)
        if 13 <= hour <= 15:
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Only trade positive drift continuation
            if asia_drift_bps < self.min_drift_bps:
                return None

            # Filter against hostile market regimes
            crisis_score = ctx.market.get("crisis_score", 0.0)
            regime = ctx.market.get("regime", "NORMAL")
            if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
                return None

            # Trend alignment check (price above EMA24)
            ema24 = self._ema(closes, 24)
            current_price = ctx.bar.close
            if ema24 is not None and current_price < ema24 * 0.995:
                return None

            # Confidence scaling based on drift magnitude and sentiment
            fear_greed = ctx.features.get("fear_greed_index", 50)
            drift_bonus = min(0.20, (asia_drift_bps - self.min_drift_bps) / 200.0)
            sentiment_bonus = 0.05 if fear_greed >= 45 else -0.05
            confidence = max(0.50, min(0.90, self.min_confidence + drift_bonus + sentiment_bonus))

            self.traded_today = True

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "asia_positive_drift_us_open_continuation",
                    "asia_drift_bps": round(asia_drift_bps, 2),
                    "asia_open": self.asia_open_price,
                    "asia_close": self.asia_close_price,
                    "ema24": round(ema24, 2) if ema24 else 0.0,
                    "fear_greed": fear_greed,
                    "hour": hour,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Mandatory cooldown: wait at least 3 bars after any position closes
        self.cooldown_until_bar = ctx.bar_index + 3