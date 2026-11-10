from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift US Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours (US session hold)
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.asia_open_price = None
        self.last_exit_bar = -100
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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour
        current_close = ctx.bar.close

        # Track session opening price at 00:00 UTC (Asia open)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Position exit rule: close before next Asia session begins (UTC 22 or 23)
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "end_of_us_session_exit",
                        "hour": current_hour,
                        "close": current_close
                    }
                )
            return None

        # Check cooldown and one-trade-per-day condition
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.last_traded_day == current_day:
            return None

        # Entry window: US session open (UTC 13 or 14)
        if current_hour in (13, 14) and self.asia_open_price is not None:
            # Measure Asia session drift (return from Asia open to current pre-US level)
            drift_pct = (current_close - self.asia_open_price) / self.asia_open_price

            # Regime filters based on prior evidence: avoid Meltdown/Crisis, ensure non-bear trend
            market_regime = ctx.market.get("regime", "NORMAL")
            trend_regime = ctx.market.get("trend_regime", "neutral")
            fear_greed = ctx.features.get("fear_greed_index", 50)

            if market_regime in ("CRISIS", "MELTDOWN"):
                return None

            if trend_regime == "bear" and fear_greed < 25:
                return None

            # Price trend confirmation: price above EMA 50 to confirm broader positive momentum
            ema50 = self._ema(closes, 50)
            if ema50 is None:
                return None

            # Entry trigger: positive Asia drift of at least 0.25% (25 bps) and holding above EMA50
            if drift_pct >= 0.0025 and current_close > ema50:
                self.last_traded_day = current_day
                confidence = min(0.9, 0.5 + float(drift_pct) * 10)
                
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_drift_continuation_us_open",
                        "drift_pct": round(drift_pct * 100, 3),
                        "asia_open": self.asia_open_price,
                        "ema50": round(ema50, 2),
                        "fear_greed": fear_greed,
                        "trend_regime": trend_regime
                    }
                )

        return None