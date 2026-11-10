from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.min_mom_bps = 50.0
        self.entry_hour = 13
        self.exit_hour = 21
        self.last_traded_day = -1
        self.cooldown_until_bar = 0

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 20:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Mandatory exit before session close
        if ctx.has_position():
            if current_hour >= self.exit_hour or current_hour < self.entry_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "session_close_cutoff",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Entry window strictly at session open (UTC 13-14), 1 trade per day
        if current_hour not in (13, 14):
            return None

        if self.last_traded_day == current_day:
            return None

        # Prior evidence failure fix: filter out extreme crisis or choppy non-trending regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.4:
            return None

        # Trend and momentum calculation
        sma30 = self._sma(closes, 30)
        if sma30 is None:
            return None

        past_close = closes[-self.lookback]
        current_close = closes[-1]
        mom_bps = ((current_close - past_close) / past_close) * 10000.0

        # Long continuation setup: strong prior 12h drift + aligned with intermediate trend
        if mom_bps > self.min_mom_bps and current_close > sma30:
            self.last_traded_day = current_day
            confidence = min(0.85, 0.55 + (mom_bps / 500.0) * 0.25)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "sma30": round(sma30, 2),
                    "close": current_close,
                    "hour": current_hour,
                }
            )

        # Short continuation setup: strong downward drift + aligned below trend
        if mom_bps < -self.min_mom_bps and current_close < sma30:
            self.last_traded_day = current_day
            confidence = min(0.85, 0.55 + (abs(mom_bps) / 500.0) * 0.25)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "sma30": round(sma30, 2),
                    "close": current_close,
                    "hour": current_hour,
                }
            )

        return None