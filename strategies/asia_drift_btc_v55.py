from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 25200,  # ~7 hours (hold from US open ~13:00 to ~20:00 UTC)
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.asia_open_price = None
        self.asia_close_price = None
        self.cooldown_until_bar = 0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        current_hour = dt.hour
        current_day = dt.toordinal()

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open
        elif current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Mandatory exit before next Asia session or at end of US session (e.g. 21:00 UTC)
        if ctx.has_position() and current_hour in (21, 22, 23):
            return ctx.signal("flat", confidence=0.7, metadata={
                "reason": "end_of_us_session_exit",
                "hour": current_hour,
                "price": ctx.bar.close
            })

        # Do not enter if in cooldown or already traded today or currently have a position
        if ctx.has_position():
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_traded_day == current_day:
            return None

        # Entry window: Early US session open (UTC 13:00 - 14:00)
        if current_hour in (13, 14):
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate Asia session return
            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Filter: Require clear, significant Asia drift (at least +30 bps, not marginal)
            if asia_drift_bps < 30.0:
                return None

            # Trend & Volatility confirmation to reject false breakouts
            ema20 = self._ema(closes, 20)
            ema50 = self._ema(closes, 50)
            if ema20 is None or ema50 is None:
                return None

            # Price must be respecting short-term trend
            if ctx.bar.close < ema20 or ema20 < ema50:
                return None

            # Check regime to avoid meltdowns/extreme crisis
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Size confidence dynamically based on drift strength
            confidence = min(0.85, max(0.55, 0.55 + (asia_drift_bps / 200.0) * 0.3))

            self.last_traded_day = current_day
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "asia_drift_us_continuation",
                    "asia_drift_bps": round(asia_drift_bps, 1),
                    "ema20": round(ema20, 1),
                    "ema50": round(ema50, 1),
                    "price": ctx.bar.close,
                    "regime": regime
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown of 4 bars (~4 hours) post exit
        self.cooldown_until_bar = ctx.bar_index + 4