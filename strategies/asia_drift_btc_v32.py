from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftRegimeContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftRegimeContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold time
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_traded_day: Optional[int] = None
        self.last_exit_bar: int = -50

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour
        price = ctx.bar.close

        # Track Asia open price (UTC 00:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Exit condition: close position before the next Asia session opens (UTC 23:00)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": price}
                )
            return None

        # Mandatory cooldown between trades
        if ctx.bar_index - self.last_exit_bar < 4:
            return None

        # Limit to 1 trade per calendar day
        if self.last_traded_day == current_day:
            return None

        # US session open window: UTC 13:00 to 14:00
        if current_hour in (13, 14):
            if self.asia_open_price is None or self.asia_open_price <= 0:
                return None

            # 1. Asia session drift: positive drift (> +0.20% gain from Asia open)
            asia_drift_bps = ((price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # 2. Regime Filter: Edge is strongest in non-crisis, bullish/neutral regimes
            regime = ctx.market.get("regime", "NORMAL")
            trend = ctx.market.get("trend_regime", "neutral")
            if regime in ("CRISIS", "MELTDOWN") or trend == "bear":
                return None

            # 3. Macro Trend Support: Price must also be above 50-period EMA
            ema50 = self._ema(closes, 50)
            if ema50 is None or price < ema50:
                return None

            # Filter: Meaningful Asia drift threshold (20 bps to 250 bps - avoid exhaustive blowoffs)
            if 20.0 <= asia_drift_bps <= 250.0:
                self.last_traded_day = current_day
                confidence = min(0.9, max(0.5, 0.5 + (asia_drift_bps / 500.0)))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=250.0,
                    take_profit_bps=400.0,
                    horizon_seconds=28800,
                    metadata={
                        "reason": "asia_positive_drift_us_open_continuation",
                        "asia_drift_bps": round(asia_drift_bps, 2),
                        "ema50": round(ema50, 2),
                        "price": price,
                        "trend_regime": trend,
                        "hour": current_hour
                    }
                )

        return None