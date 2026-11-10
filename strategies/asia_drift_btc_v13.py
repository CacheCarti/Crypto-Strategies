from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold max
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_traded_date: Optional[str] = None
        self.last_exit_bar: int = -20

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar = ctx.bar
        ts = bar.timestamp
        current_date = ts.strftime("%Y-%m-%d")
        hour = ts.hour

        # Mark Asia open at 00:00 UTC
        if hour == 0:
            self.asia_open_price = bar.open

        # Position management: Close before the next Asia session starts (at 23:00 UTC)
        if ctx.has_position():
            if hour == 23:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": bar.close}
                )
            return None

        # Guard: cooldown of at least 6 bars between trades
        if (ctx.bar_index - self.last_exit_bar) < 6:
            return None

        # Guard: max 1 trade per calendar day
        if self.last_traded_date == current_date:
            return None

        # Entry window: US Session open (13:00 to 15:00 UTC)
        if 13 <= hour <= 15:
            if self.asia_open_price is None or self.asia_open_price <= 0:
                return None

            asia_drift_bps = ((bar.close - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Require positive Asia drift of at least 35 bps to filter flat chop, but cap at 300 bps to avoid blowoffs
            if 35.0 <= asia_drift_bps <= 300.0:
                # Trend confirmation: price above 24h baseline (SMA 24)
                sma24 = self._sma(closes, 24)
                if sma24 is None or bar.close <= sma24:
                    return None

                # Volatility check: ensure enough active range
                highs = ctx.highs(20)
                lows = ctx.lows(20)
                atr = self._atr(highs, lows, closes, 14)
                if atr is None:
                    return None

                atr_bps = (atr / bar.close) * 10000.0
                if atr_bps < 20.0:
                    return None

                # Regime filter from market context
                regime = ctx.market.get("regime", "NORMAL")
                if regime in ("CRISIS", "MELTDOWN"):
                    return None

                self.last_traded_date = current_date
                confidence = min(0.9, max(0.55, 0.5 + (asia_drift_bps / 500.0)))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_positive_drift_continuation",
                        "asia_drift_bps": round(asia_drift_bps, 2),
                        "atr_bps": round(atr_bps, 2),
                        "sma24": round(sma24, 2),
                        "hour": hour,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index