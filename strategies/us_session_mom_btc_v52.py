from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour

        # Force session close by UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Strict 1 trade per day, entry window restricted to US market opening hours (UTC 13-15)
        if self.last_traded_date == current_date:
            return None

        if not (13 <= hour <= 15):
            return None

        # Regime Gate: Prior evidence shows edge fails in extreme crisis/high vol chop
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.4 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility & Trend alignment
        sma30 = self._sma(closes, 30)
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr14 = self._atr(highs, lows, closes, 14)

        if sma30 is None or atr14 is None:
            return None

        curr_close = closes[-1]
        lookback_12_close = closes[-13]
        ret_12 = (curr_close - lookback_12_close) / lookback_12_close

        # Momentum threshold: at least 0.6% directional move over pre-market 12 hours
        # and aligned with 30-bar baseline trend
        threshold = 0.006

        if ret_12 > threshold and curr_close > sma30:
            self.last_traded_date = current_date
            confidence = min(1.0, 0.65 + abs(ret_12) * 10.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_continuation_bull",
                    "ret_12": round(ret_12, 4),
                    "sma30": round(sma30, 2),
                    "price": curr_close,
                    "hour": hour
                }
            )

        if ret_12 < -threshold and curr_close < sma30:
            self.last_traded_date = current_date
            confidence = min(1.0, 0.65 + abs(ret_12) * 10.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_continuation_bear",
                    "ret_12": round(ret_12, 4),
                    "sma30": round(sma30, 2),
                    "price": curr_close,
                    "hour": hour
                }
            )

        return None