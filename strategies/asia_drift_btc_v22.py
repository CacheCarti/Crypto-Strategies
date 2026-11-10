from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.bars_since_close = 10

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_close = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        self.bars_since_close += 1
        ts = ctx.bar.timestamp
        hour = ts.hour
        day_key = ts.toordinal()

        # Track Asia session (00:00 - 08:00 UTC)
        if hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None
        elif hour == 8:
            self.asia_close_price = ctx.bar.close

        # Mandatory exit: flat before the next Asia session opens (at 23:00 UTC)
        if ctx.has_position():
            if hour == 23:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard: at least 3 bars since last exit
        if self.bars_since_close < 3:
            return None

        # Entry window: US Session Open (~13:00 to 14:00 UTC)
        # Limit to 1 trade per calendar day
        if 13 <= hour <= 14 and self.last_traded_day != day_key:
            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_return_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

                # Regime filter from market context and trend alignment
                trend_regime = ctx.market.get("trend_regime", "neutral")
                crisis_score = ctx.market.get("crisis_score", 0.0)
                sma50 = self._sma(closes, 50)

                # Prior evidence: selective entries in non-crisis, trend-aligned environments
                # Asia drift must be solidly positive (> 25 bps) but not overextended (< 350 bps)
                drift_valid = 25.0 <= asia_return_bps <= 350.0
                regime_safe = crisis_score < 0.4 and trend_regime != "bear"
                trend_aligned = sma50 is not None and ctx.bar.close > sma50

                if drift_valid and regime_safe and trend_aligned:
                    self.last_traded_day = day_key
                    confidence = 0.7 if trend_regime == "bull" else 0.55

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=250.0,
                        take_profit_bps=400.0,
                        horizon_seconds=32400,
                        metadata={
                            "reason": "asia_positive_drift_us_continuation",
                            "asia_return_bps": round(asia_return_bps, 2),
                            "hour": hour,
                            "trend_regime": trend_regime,
                            "sma50": round(sma50, 2),
                            "price": ctx.bar.close
                        }
                    )

        return None