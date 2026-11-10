from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours maximum hold (13:00 to 20:00 UTC)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 15)
        if len(closes) < self.lookback + 15:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_of_year = current_time.timetuple().tm_yday

        # Hard session close: exit all positions before 21:00 UTC
        if hour >= 21 or hour < 12:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_close_pre_21utc",
                    "hour": hour,
                    "close": ctx.bar.close
                })
            return None

        # Check for open position within session (hold until 21 or hit SL/TP)
        if ctx.has_position():
            return None

        # Check cooldown and one-trade-per-day restriction
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_day == day_of_year:
            return None

        # Entry window: precisely at US cash open window (13:00 or 14:00 UTC)
        if hour not in (13, 14):
            return None

        # Regime Gate: Prior failures suffered from choppy/meltdown environments.
        # Filter out extreme stress and meltdowns; require aligned market structure
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.40:
            return None

        # Measure pre-session 12-hour momentum
        past_close = closes[-self.lookback - 1]
        current_close = closes[-1]
        mom_pct = (current_close - past_close) / past_close

        # Volatility normalization via ATR
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        atr_pct = atr / current_close
        threshold = max(0.0075, 0.75 * atr_pct)

        # Fear & Greed sentiment confirmation
        fg = ctx.features.get("fear_greed_index", 50.0)

        # Long continuation: Strong positive momentum + non-extreme sentiment
        if mom_pct > threshold and fg < 80:
            self.last_traded_day = day_of_year
            self.cooldown_until_bar = ctx.bar_index + 8
            conf = min(0.85, 0.55 + (mom_pct / (2 * threshold)) * 0.25)
            return ctx.signal("long", confidence=conf, metadata={
                "reason": "us_open_bullish_momentum_continuation",
                "mom_pct": round(mom_pct, 4),
                "threshold": round(threshold, 4),
                "hour": hour,
                "fg_index": fg,
                "atr": round(atr, 2)
            })

        # Short continuation: Strong downward momentum + sentiment not rock bottom
        if mom_pct < -threshold and fg > 20:
            self.last_traded_day = day_of_year
            self.cooldown_until_bar = ctx.bar_index + 8
            conf = min(0.85, 0.55 + (abs(mom_pct) / (2 * threshold)) * 0.25)
            return ctx.signal("short", confidence=conf, metadata={
                "reason": "us_open_bearish_momentum_continuation",
                "mom_pct": round(mom_pct, 4),
                "threshold": round(threshold, 4),
                "hour": hour,
                "fg_index": fg,
                "atr": round(atr, 2)
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce minimum cooldown of 4 bars after any trade exit
        self.cooldown_until_bar = ctx.bar_index + 4