from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold (US session drift)
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_day: int = -1
        self.last_exit_bar: int = -999
        self.cooldown_bars: int = 4

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1.0 - k)
        return val

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Mandatory Exit: Close before the next Asia session starts (around 22:00-23:00 UTC)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Guard: Check cooldown and single trade per calendar day
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if self.last_trade_day == current_day:
            return None

        # Entry window: US morning open (UTC 13:00 - 15:00)
        if 13 <= current_hour <= 15:
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate session drift
            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Dynamic drift threshold normalized by ATR volatility
            highs = ctx.highs(30)
            lows = ctx.lows(30)
            atr_val = self._atr(highs, lows, closes, 14)
            if atr_val is None or atr_val <= 0:
                return None

            atr_bps = (atr_val / ctx.bar.close) * 10000.0
            # Drift must be meaningfully positive (at least 0.25x ATR) but not hyper-extended (< 2.5x ATR)
            drift_ratio = asia_drift_bps / atr_bps

            if drift_ratio >= 0.25 and drift_ratio <= 2.50:
                # Trend filter: Price should be above 20-period EMA
                ema20 = self._ema(closes, 20)
                if ema20 is not None and ctx.bar.close > ema20:
                    # Gating on broad market stress
                    crisis_score = ctx.market.get("crisis_score", 0.0)
                    if crisis_score > 0.65:
                        return None

                    fear_greed = ctx.features.get("fear_greed_index", 50.0)
                    if fear_greed < 20.0:  # Avoid entering during peak panics
                        return None

                    # Scale confidence based on drift quality and market sentiment
                    confidence = min(0.9, max(0.45, 0.5 + 0.15 * min(drift_ratio, 2.0)))
                    self.last_trade_day = current_day

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_drift_continuation_us_open",
                            "asia_drift_bps": round(asia_drift_bps, 2),
                            "drift_ratio": round(drift_ratio, 2),
                            "atr_bps": round(atr_bps, 2),
                            "ema20": round(ema20, 2),
                            "price": ctx.bar.close,
                            "hour": current_hour,
                        }
                    )

        return None