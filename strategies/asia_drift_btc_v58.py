from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 25200,  # ~7 hours hold (US session to Asia open)
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_traded_date = None
        self.cooldown_until_bar = 0
        self.min_drift_bps = 35.0   # Require clear positive drift to beat friction
        self.max_drift_bps = 250.0  # Avoid chasing exhausted blow-offs

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce hard cooldown after position exits
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour
        price = ctx.bar.close

        # Record Asia session open (UTC 00:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        # Scheduled exit before the next Asia open (UTC 23:00)
        if ctx.has_position() and hour == 23:
            return ctx.signal(
                "flat",
                confidence=0.6,
                metadata={
                    "reason": "session_close_pre_asia",
                    "price": price,
                    "hour": hour,
                }
            )

        # Market regime filter (avoid entering long during extreme crisis)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.75:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": crisis_score}
                )
            return None

        # US Session Open Trigger (UTC 13:00 - 14:00)
        # Check if already traded today, in cooldown, or already positioned
        if ctx.has_position():
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_traded_date == current_date:
            return None

        if hour in (13, 14) and self.asia_open_price is not None:
            # Measure Asia drift from 00:00 to current bar
            drift_bps = ((price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Trend baseline check via EMA30
            ema30 = self._ema(closes, 30)
            if ema30 is None:
                return None

            # ATR for dynamic volatility check
            atr = self._atr(highs, lows, closes, 14)
            atr_bps = (atr / price * 10000.0) if (atr and price > 0) else 100.0

            # Condition: Clear positive Asia drift within bounded range, and price confirming above EMA30
            if self.min_drift_bps <= drift_bps <= self.max_drift_bps and price > ema30:
                self.last_traded_date = current_date
                
                # Dynamic stop-loss and take-profit derived from current volatility
                sl_bps = max(160.0, min(300.0, atr_bps * 1.5))
                tp_bps = max(260.0, min(480.0, atr_bps * 2.5))
                confidence = min(0.9, max(0.55, 0.5 + (drift_bps / 500.0)))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_drift_continuation_us_open",
                        "drift_bps": round(drift_bps, 2),
                        "asia_open": self.asia_open_price,
                        "ema30": round(ema30, 2),
                        "atr_bps": round(atr_bps, 2),
                        "hour": hour,
                    }
                )

        return None