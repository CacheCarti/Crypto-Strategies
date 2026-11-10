from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until late US session
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_traded_day: Optional[int] = None
        self.last_exit_bar: int = -50
        self.cooldown_bars: int = 8
        self.min_drift_bps: float = 35.0  # Clear directional push required
        self.max_drift_bps: float = 350.0 # Avoid exhausted parabolic pushes

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None

        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Mandated time-based exit before next Asia open (UTC 22:00 - 23:00)
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "end_of_us_session_timed_exit",
                        "hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard: ensure distance between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # One trade per day limit
        if self.last_traded_day == current_day:
            return None

        # Entry window: US morning open (UTC 13:00 to 14:00)
        if 13 <= current_hour <= 14:
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Only trade if Asia demonstrated a clean, sustainable bullish drift
            if self.min_drift_bps <= drift_bps <= self.max_drift_bps:
                ema20 = self._ema(closes, 20)
                atr = self._atr(highs, lows, closes, 14)
                if ema20 is None or atr is None:
                    return None

                # Confirmation: US price must be trading above the 20-EMA (local structure support)
                if ctx.bar.close < ema20:
                    return None

                # Macro safety: avoid entering into extreme fear liquidity cascades
                fear_greed = ctx.features.get("fear_greed_index", 50)
                if fear_greed < 20:
                    return None

                # Dynamic structure-based SL/TP using current ATR
                atr_bps = (atr / ctx.bar.close) * 10000.0
                dynamic_sl = max(150.0, min(350.0, atr_bps * 1.5))
                dynamic_tp = max(250.0, min(500.0, atr_bps * 2.5))

                confidence = min(0.9, max(0.5, 0.5 + (drift_bps / 500.0)))
                self.last_traded_day = current_day

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=dynamic_sl,
                    take_profit_bps=dynamic_tp,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_bullish_drift_continuation",
                        "drift_bps": round(drift_bps, 2),
                        "asia_open": self.asia_open_price,
                        "asia_close": self.asia_close_price,
                        "ema20": round(ema20, 2),
                        "atr_bps": round(atr_bps, 2),
                        "fear_greed": fear_greed
                    }
                )

        return None