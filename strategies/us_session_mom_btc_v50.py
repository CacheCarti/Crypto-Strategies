from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.entry_hour = 13  # US open window (13:00 UTC)
        self.close_hour = 21  # Close before US session end
        self.min_return_bps = 50.0  # Clear directional move threshold (0.50%)
        self.last_traded_day = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 15:
            return None

        dt = ctx.bar.timestamp
        current_day = (dt.year, dt.month, dt.day)
        hour = dt.hour

        # Hard session exit: force flat at or after close_hour (21 UTC) or outside active window
        if ctx.has_position():
            if hour >= self.close_hour or hour < self.entry_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_cutoff",
                        "hour": hour,
                        "close_price": ctx.bar.close,
                    }
                )
            return None

        # Entry logic: strictly fire only once per day at the designated entry hour
        if hour != self.entry_hour:
            return None

        if self.last_traded_day == current_day:
            return None

        # Market regime filter: avoid extreme crisis environments
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Measure pre-session 12-hour momentum
        start_price = closes[-self.lookback]
        current_price = closes[-1]
        raw_return_bps = ((current_price - start_price) / start_price) * 10000.0

        # Dynamic volatility check using ATR
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None
        
        atr_bps = (atr / current_price) * 10000.0
        # If market is completely dead or hyper-volatile, skip
        if atr_bps < 15.0 or atr_bps > 250.0:
            return None

        # Fear and greed sanity filter: don't chase long into extreme greed, or short into extreme fear
        fg = ctx.features.get("fear_greed_index", 50)

        # Bullish continuation: clear upward momentum over the last 12 hours
        if raw_return_bps >= self.min_return_bps and fg < 85:
            self.last_traded_day = current_day
            confidence = min(0.85, 0.55 + (abs(raw_return_bps) / 400.0) * 0.3)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_long_continuation",
                    "pre_session_return_bps": raw_return_bps,
                    "atr_bps": atr_bps,
                    "hour": hour,
                    "fear_greed": fg,
                }
            )

        # Bearish continuation: clear downward momentum over the last 12 hours
        if raw_return_bps <= -self.min_return_bps and fg > 15:
            self.last_traded_day = current_day
            confidence = min(0.85, 0.55 + (abs(raw_return_bps) / 400.0) * 0.3)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_short_continuation",
                    "pre_session_return_bps": raw_return_bps,
                    "atr_bps": atr_bps,
                    "hour": hour,
                    "fear_greed": fg,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass