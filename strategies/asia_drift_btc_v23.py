from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 25200,  # ~7 hours (exit before next Asia session)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_traded_date: Optional[str] = None
        self.last_exit_bar: int = -50

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_date = current_time.strftime("%Y-%m-%d")

        # Track Asia open at UTC 00:00
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Close position before the next Asia session opens (UTC 22 or 23)
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "bar_index": ctx.bar_index}
                )
            return None

        # Mandatory cooldown check (at least 4 bars since last exit)
        if ctx.bar_index - self.last_exit_bar < 4:
            return None

        # Only one trade per calendar day, evaluated at US open / early US session (UTC 13-15)
        if self.last_traded_date == current_date:
            return None

        if not (13 <= current_hour <= 15):
            return None

        if self.asia_open_price is None or self.asia_open_price <= 0:
            return None

        # Measure Asia session drift (evaluated against current bar's close/open)
        drift_pct = (ctx.bar.close - self.asia_open_price) / self.asia_open_price

        # Thresholds: Asia must drift up at least +0.30% to confirm continuation
        if drift_pct <= 0.0030:
            return None

        # Technical confirmation: Trend alignment via EMA-24 (approx 1-day trend)
        ema24 = self._ema(closes, 24)
        if ema24 is None or ctx.bar.close < ema24:
            return None

        # Volatility check: avoid entering in absolute panic/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic stop loss based on ATR to prevent premature trailing stops
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        sl_bps = 220.0
        if atr is not None and ctx.bar.close > 0:
            atr_bps = (atr / ctx.bar.close) * 10000.0 * 1.5
            sl_bps = min(max(atr_bps, 160.0), 320.0)

        tp_bps = sl_bps * 1.8
        self.last_traded_date = current_date

        return ctx.signal(
            "long",
            confidence=min(0.65 + drift_pct * 10.0, 0.95),
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=25200,
            metadata={
                "reason": "asia_drift_continuation_us_open",
                "asia_open": self.asia_open_price,
                "current_price": ctx.bar.close,
                "drift_pct": round(drift_pct, 4),
                "ema24": round(ema24, 2),
                "hour": current_hour,
                "sl_bps": round(sl_bps, 1)
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index