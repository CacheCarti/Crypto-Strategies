from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUSContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # 8 hours typical hold (14:00 to 22:00 UTC)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_day: Optional[int] = None
        self.last_exit_bar: int = -50

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        current_hour = ts.hour
        current_day = ts.toordinal()

        # Track Asia Session (UTC 00:00 to 08:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Mandatory Exit: Close before the next Asia session begins (UTC 22:00 or 23:00)
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_pre_asia",
                        "hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Mandatory cooldown check (at least 6 bars ~6h after last exit)
        if (ctx.bar_index - self.last_exit_bar) < 6:
            return None

        # One trade per calendar day guard
        if self.last_trade_day == current_day:
            return None

        # Entry window: US Session Open (UTC 13:00 or 14:00)
        if current_hour in (13, 14):
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate Asia Drift
            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Condition 1: Meaningful positive Asia drift (> 25 bps, but not overextended < 350 bps)
            if not (25.0 <= asia_drift_bps <= 350.0):
                return None

            # Confirmation 1: Trend Alignment via 24-period EMA
            ema24 = self._ema(closes, 24)
            if ema24 is None or ctx.bar.close <= ema24:
                return None

            # Confirmation 2: Volatility state via ATR (avoid dead/stagnant or meltdown markets)
            highs = ctx.highs(35)
            lows = ctx.lows(35)
            atr14 = self._atr(highs, lows, closes, 14)
            if atr14 is None:
                return None
            atr_pct = (atr14 / ctx.bar.close) * 100.0
            if atr_pct < 0.25 or atr_pct > 3.0:
                return None

            # Confirmation 3: Cross-asset & market regime check
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Dynamic confidence based on drift strength
            confidence = min(0.85, 0.55 + (asia_drift_bps / 1000.0))

            self.last_trade_day = current_day
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "asia_drift_us_continuation",
                    "asia_drift_bps": round(asia_drift_bps, 2),
                    "ema24": round(ema24, 2),
                    "atr_pct": round(atr_pct, 4),
                    "regime": regime,
                    "hour": current_hour
                }
            )

        return None