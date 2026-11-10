from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold from US open to Asia start
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.asia_open_price = None
        self.last_exit_bar = -10
        self.cooldown_bars = 4

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Track session opening price for Asia at UTC 00:00
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # 1. Session Exit Logic: Flatten before next Asia session begins (UTC 23:00)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": ctx.bar.close}
                )

        # 2. Cooldown and frequency guard: Only 1 trade setup evaluated per UTC day
        if self.last_traded_day == current_day:
            return None
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 3. Entry Window: US session kickoff (UTC 13:00 to 14:00)
        if 13 <= current_hour <= 14 and not ctx.has_position():
            if self.asia_open_price is None or self.asia_open_price <= 0:
                # Fallback to bar 13 bars ago if session boundary occurred before initialization
                self.asia_open_price = closes[-14]

            asia_return_pct = (ctx.bar.close - self.asia_open_price) / self.asia_open_price
            atr = self._atr(highs, lows, closes, 14)
            sma_trend = self._sma(closes, 48)

            if atr is None or sma_trend is None:
                return None

            atr_bps = (atr / ctx.bar.close) * 10000.0
            min_drift_threshold = max(0.002, (atr / ctx.bar.close) * 0.4)

            # Robustness: Require Asia drift to exceed dynamic volatility floor + aligned with medium trend
            if asia_return_pct > min_drift_threshold and ctx.bar.close > sma_trend:
                # Dynamic stop loss based on recent ATR
                sl_bps = max(150.0, min(350.0, atr_bps * 1.5))
                tp_bps = sl_bps * 1.8
                
                confidence = min(0.85, max(0.55, 0.5 + (asia_return_pct * 15.0)))
                self.last_traded_day = current_day

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=28800,
                    metadata={
                        "reason": "asia_drift_continuation_us_open",
                        "asia_return_pct": round(asia_return_pct, 4),
                        "atr_bps": round(atr_bps, 1),
                        "sma_48": round(sma_trend, 2),
                        "hour": current_hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index