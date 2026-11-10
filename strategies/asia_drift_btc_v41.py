from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.cooldown_until_bar = 0
        self.asia_open_price = None
        self.current_day = -1

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        hour = ts.hour
        day_id = ts.toordinal()

        # Track session opening price at 00:00 UTC
        if hour == 0 and day_id != self.current_day:
            self.asia_open_price = ctx.bar.open
            self.current_day = day_id

        # Mandatory exit: Close before the next Asia session starts (at 22:00 or 23:00 UTC)
        if ctx.has_position():
            if hour in (22, 23):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # One trade per day limit
        if day_id == self.last_trade_day:
            return None

        # Entry window: Early US session (13:00 to 15:00 UTC)
        if 13 <= hour <= 15:
            if self.asia_open_price is None:
                return None

            # Calculate Asia drift: return from 00:00 open to end of Asia session (assessed by current price)
            drift_bps = ((ctx.bar.close - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Condition 1: Positive drift with minimum threshold (>= 35 bps) to clear friction
            if drift_bps < 35.0:
                return None

            # Condition 2: Trend alignment - price must be above 30-period EMA
            ema_val = self._ema(closes, 30)
            if ema_val is None or ctx.bar.close <= ema_val:
                return None

            # Condition 3: Avoid entering in high crisis/panic regimes
            fear_greed = ctx.features.get("fear_greed_index", 50)
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN") or fear_greed < 20:
                return None

            # Volatility-based adaptive dynamic confidence
            highs = ctx.highs(20)
            lows = ctx.lows(20)
            atr = self._atr(highs, lows, closes, 14)
            atr_pct = (atr / ctx.bar.close) if atr else 0.01

            conf = 0.65
            if drift_bps > 100.0:
                conf += 0.1
            if fear_greed >= 50:
                conf += 0.05
            conf = min(0.9, max(0.5, conf))

            self.last_trade_day = day_id

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "asia_drift_continuation_us_open",
                    "drift_bps": round(drift_bps, 2),
                    "ema_30": round(ema_val, 2),
                    "close": ctx.bar.close,
                    "fear_greed": fear_greed,
                    "atr_pct": round(atr_pct * 100, 3)
                }
            )

        return None