from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftRegimeContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftRegimeContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold through US session
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.asia_open_price: Optional[float] = None
        self.asia_drift_pct: float = 0.0
        self.last_exit_bar = -10
        self.cooldown_bars = 4

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        ts = ctx.bar.timestamp
        hour = ts.hour
        day = ts.toordinal()

        # Record Asia open at UTC 00:00
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        # Record Asia drift at the end of the Asia session (UTC 08:00)
        if hour == 8 and self.asia_open_price is not None and self.asia_open_price > 0:
            self.asia_drift_pct = (ctx.bar.close - self.asia_open_price) / self.asia_open_price

        # Exit condition: Close before the next Asia session (UTC 22:00 or 23:00)
        if ctx.has_position():
            if hour >= 22 or hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: US session open (UTC 13:00 to 15:00)
        # Limit to 1 entry per calendar day
        if 13 <= hour <= 15 and self.last_traded_day != day:
            # Macro / Market regime gate: avoid crisis / high volatility breakdowns
            mkt_regime = ctx.market.get("regime", "NORMAL")
            crisis_score = ctx.market.get("crisis_score", 0.0)
            if mkt_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
                return None

            # Slow sentiment gate: avoid severe panic where drifts fail continuation
            fear_greed = ctx.features.get("fear_greed_index", 50)
            if fear_greed < 20:
                return None

            # Price action confirmation: Trend alignment via EMA24 (~1 day trend)
            ema24 = self._ema(closes, 24)
            if ema24 is None:
                return None

            # Require positive drift during Asia session (> +0.3% drift) and price above 24h trend
            drift_threshold = 0.003
            if self.asia_drift_pct >= drift_threshold and ctx.bar.close > ema24:
                self.last_traded_day = day
                confidence = min(1.0, 0.5 + (self.asia_drift_pct * 20.0))
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=250.0,
                    take_profit_bps=400.0,
                    horizon_seconds=28800,
                    metadata={
                        "reason": "asia_drift_continuation_us_open",
                        "asia_drift_pct": round(self.asia_drift_pct, 4),
                        "ema24": round(ema24, 2),
                        "price": ctx.bar.close,
                        "fear_greed": fear_greed,
                        "hour": hour,
                    }
                )

        return None