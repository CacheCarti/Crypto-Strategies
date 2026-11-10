from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # Max intraday hold ~6 hours
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.atr_period = 14
        self.last_traded_day = -1
        self.min_abs_return_bps = 50.0  # Momentum threshold over 12h
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)

        if len(closes) < self.lookback + 1 or len(highs) < self.atr_period + 1:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Hard session close: flatten before UTC 21 (at UTC 20 or 21)
        if ctx.has_position():
            if current_hour >= 20 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timed_exit", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Check entry window: US session open (UTC 13 or 14)
        if current_hour not in (13, 14):
            return None

        # Gate: One trade per calendar day & check cooldown
        if self.last_traded_day == current_day or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Filter: Avoid extreme crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 12-bar pre-session price direction
        p_now = closes[-1]
        p_prev = closes[-self.lookback - 1]
        return_bps = ((p_now - p_prev) / p_prev) * 10000.0

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr == 0:
            return None

        # Dynamic volatility stop: 2.0x ATR converted to basis points
        atr_bps = (atr / p_now) * 10000.0
        sl_bps = max(120.0, min(350.0, atr_bps * 1.8))
        tp_bps = max(240.0, min(700.0, sl_bps * 2.0))

        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Long continuation setup
        if return_bps >= self.min_abs_return_bps and fg_index > 25:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 4
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "return_12b_bps": round(return_bps, 1),
                    "atr_bps": round(atr_bps, 1),
                    "sl_bps": round(sl_bps, 1),
                    "fg_index": fg_index
                }
            )

        # Short continuation setup
        elif return_bps <= -self.min_abs_return_bps and fg_index < 75:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 4
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "return_12b_bps": round(return_bps, 1),
                    "atr_bps": round(atr_bps, 1),
                    "sl_bps": round(sl_bps, 1),
                    "fg_index": fg_index
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown on closure
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 2)