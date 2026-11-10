from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        highs = ctx.highs(25)
        lows = ctx.lows(25)

        if len(closes) < 25:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Exit all positions before UTC 21 or outside allowed session
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timeout", "hour": current_hour, "bar_index": ctx.bar_index}
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Regime gate to address prior OOS churn: avoid extreme crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.55:
            return None

        # Trade only once per day, exactly around US open window (UTC 13 or 14)
        if current_day == self.last_traded_day:
            return None

        if current_hour not in (13, 14):
            return None

        # Measure 12-bar pre-session momentum
        momentum_return = (closes[-1] - closes[-13]) / closes[-13]
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or closes[-1] == 0:
            return None

        atr_pct = atr / closes[-1]
        threshold = max(0.006, atr_pct * 0.75)

        fg_index = ctx.features.get("fear_greed_index", 50)

        # Bullish Continuation: Strong positive 12-bar momentum + non-extreme sentiment
        if momentum_return > threshold and fg_index <= 80:
            self.last_traded_day = current_day
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_open_continuation_long",
                    "momentum_return": round(momentum_return, 5),
                    "threshold": round(threshold, 5),
                    "hour": current_hour,
                    "fear_greed": fg_index,
                    "market_regime": market_regime
                }
            )

        # Bearish Continuation: Strong negative 12-bar momentum + non-extreme sentiment
        if momentum_return < -threshold and fg_index >= 20:
            self.last_traded_day = current_day
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_open_continuation_short",
                    "momentum_return": round(momentum_return, 5),
                    "threshold": round(threshold, 5),
                    "hour": current_hour,
                    "fear_greed": fg_index,
                    "market_regime": market_regime
                }
            )

        return None