from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold from US open to Asia start
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_day: Optional[int] = None
        self.cooldown_until_bar: int = 0
        self.min_asia_return_bps: float = 30.0  # Require meaningful Asia drift
        self.max_asia_return_bps: float = 350.0 # Avoid exhausted moves

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Cooldown of at least 4 bars after a trade exit to avoid re-triggering
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None

        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close position before next Asia session begins (e.g., at UTC 23:00)
        if ctx.has_position():
            if current_hour == 23:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "pre_asia_session_close",
                        "hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Check cooldown and one-trade-per-day restriction
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_trade_day == current_day:
            return None

        # Trigger boundary: Exact US session open (UTC 13:00 or 14:00)
        # We trigger cleanly at UTC 13:00 if valid conditions are met
        if current_hour == 13:
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate Asia session drift
            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Trend & Volatility gating
            ema20 = self._ema(closes, 20)
            ema50 = self._ema(closes, 50)
            if ema20 is None or ema50 is None:
                return None

            # Market context regime gate (avoid entering into severe crisis/meltdown)
            market_regime = ctx.market.get("regime", "NORMAL")
            if market_regime in ("CRISIS", "MELTDOWN"):
                return None

            # Event trigger: Positive meaningful Asia drift, with confirmation above EMA20
            if self.min_asia_return_bps <= asia_drift_bps <= self.max_asia_return_bps:
                if ctx.bar.close > ema20 and ema20 > ema50:
                    self.last_trade_day = current_day
                    confidence = 0.65 if asia_drift_bps > 80.0 else 0.55

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "us_open_asia_bullish_drift_continuation",
                            "asia_drift_bps": round(asia_drift_bps, 2),
                            "ema20": round(ema20, 2),
                            "ema50": round(ema50, 2),
                            "hour": current_hour
                        }
                    )

        return None