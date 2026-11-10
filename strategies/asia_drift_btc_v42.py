from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftRegimeContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftRegimeContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold from US open to before Asia session
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_traded_date: Optional[str] = None
        self.cooldown_bars: int = 4
        self.last_exit_bar: int = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_time = ctx.bar.timestamp
        current_date_str = current_time.strftime("%Y-%m-%d")
        current_hour = current_time.hour
        close_price = ctx.bar.close

        # Track session opening price at UTC 00:00
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # If holding a position, check for session exit condition (UTC 22:00 - 23:00 prior to next Asia)
        if ctx.has_position():
            if current_hour >= 22:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "end_of_us_session_exit",
                        "hour": current_hour,
                        "close": close_price,
                    }
                )
            return None

        # Check cooldown from last exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # One trade per calendar day maximum
        if self.last_traded_date == current_date_str:
            return None

        # Only evaluate entry around US Session open (UTC 13:00 or 14:00)
        if current_hour not in (13, 14):
            return None

        if self.asia_open_price is None:
            return None

        # Calculate Asia session drift %
        drift_pct = (close_price - self.asia_open_price) / self.asia_open_price

        # Edge Filter 1: Meaningful Asia upward drift (> 0.25% to filter flat noise)
        if drift_pct < 0.0025:
            return None

        # Edge Filter 2: Macro/Regime gating
        # Market context regime must not be crisis or meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Sentiment gate: Do not enter drift continuations under extreme fear exhaustion
        fng = ctx.features.get("fear_greed_index", 50)
        if fng < 25:
            return None

        # Trend alignment: Ensure price is holding above intermediate trend (EMA 34)
        ema34 = self._ema(closes, 34)
        if ema34 is None or close_price < ema34:
            return None

        # Volatility check via ATR
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None

        atr_pct = (atr / close_price) * 10000.0  # in bps
        stop_bps = max(180.0, min(350.0, atr_pct * 1.5))
        tp_bps = stop_bps * 1.6

        # Confidence scaled by strength of drift and sentiment
        confidence = 0.65
        if drift_pct > 0.0075:
            confidence += 0.1
        if fng > 55:
            confidence += 0.1
        confidence = min(0.9, confidence)

        self.last_traded_date = current_date_str

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=stop_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=28800,
            metadata={
                "reason": "asia_drift_bullish_continuation",
                "drift_pct": round(drift_pct * 100, 3),
                "asia_open": self.asia_open_price,
                "current_price": close_price,
                "ema34": round(ema34, 2),
                "fng": fng,
                "regime": market_regime,
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index