from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until Asia open
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.asia_open_price: Optional[float] = None
        self.asia_drift_pct: float = 0.0

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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour
        close_px = ctx.bar.close

        # Track session opening price at 00:00 UTC
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        # Close position before next Asia session begins (UTC 23:00)
        if ctx.has_position():
            if hour == 23:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "close": close_px}
                )
            return None

        # Cooldown: enforce strictly 1 trade per day
        if self.last_traded_date == current_date:
            return None

        # Entry window at US cash open (UTC 13:00 - 14:00)
        if hour in (13, 14):
            if self.asia_open_price is None:
                # Fallback to 13-bar lookback as start of day approximation
                self.asia_open_price = closes[-14]

            drift_pct = (close_px - self.asia_open_price) / self.asia_open_price

            # Regime and momentum confirmation to avoid single lucky trade failures
            ema24 = self._ema(closes, 24)
            highs = ctx.highs(15)
            lows = ctx.lows(15)
            atr = self._atr(highs, lows, closes, 14)

            # Require positive Asia drift of at least +0.25%, price above 24h EMA, and non-crisis regime
            regime = ctx.market.get("regime", "NORMAL")
            if drift_pct >= 0.0025 and ema24 is not None and close_px > ema24 and regime not in ("CRISIS", "MELTDOWN"):
                self.last_traded_date = current_date

                # Dynamic SL/TP based on volatility if available
                sl_bps = 220.0
                tp_bps = 360.0
                if atr and close_px > 0:
                    vol_bps = (atr / close_px) * 10000.0
                    sl_bps = max(160.0, min(300.0, vol_bps * 1.5))
                    tp_bps = max(250.0, min(480.0, vol_bps * 2.5))

                conf = min(0.9, max(0.5, 0.5 + drift_pct * 15.0))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=36000,
                    metadata={
                        "reason": "asia_drift_continuation",
                        "drift_pct": round(drift_pct, 4),
                        "asia_open": self.asia_open_price,
                        "ema24": round(ema24, 2),
                        "close": close_px,
                        "hour": hour
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass