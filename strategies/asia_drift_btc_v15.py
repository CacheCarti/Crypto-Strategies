from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from 13:00 to 20:00 UTC)
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.current_trade_day: Optional[int] = None
        self.last_exit_bar = -999
        self.cooldown_bars = 4

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

        bar = ctx.bar
        ts = bar.timestamp
        hour = ts.hour
        day_id = ts.year * 1000 + ts.timetuple().tm_yday

        # Reset session tracking at UTC 00:00
        if hour == 0:
            self.asia_open_price = bar.open
            self.asia_close_price = None

        # Capture end of Asia session at UTC 08:00
        if hour == 8 and self.asia_open_price is not None:
            self.asia_close_price = bar.close

        # Mandatory session exit before the next Asia session starts (e.g. at 21:00 UTC)
        if ctx.has_position():
            if hour >= 21 or hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "bar_index": ctx.bar_index}
                )
            return None

        # Guard: cooldown after previous exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Only one trade per calendar day; entry strictly during US open window (13:00 to 15:00 UTC)
        if self.current_trade_day == day_id:
            return None

        if not (13 <= hour <= 15):
            return None

        # Must have completed Asia session observations
        if self.asia_open_price is None or self.asia_close_price is None:
            return None

        # Calculate Asia session drift in bps
        asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

        # Require a clear, positive drift (between +35 bps and +250 bps) to filter flat or overextended days
        if not (35.0 <= asia_drift_bps <= 250.0):
            return None

        # Price-action filters: must trade above EMA(40) to align with macro swing trend
        ema_trend = self._ema(closes, 40)
        if ema_trend is None or bar.close <= ema_trend:
            return None

        # Regime & sentiment confirmation: filter out high crisis or extreme market panic
        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 25:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility check: current ATR must be healthy
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or (atr / bar.close) < 0.002:
            return None

        # Mark day as traded
        self.current_trade_day = day_id

        # Confidence scaled by drift strength
        confidence = min(0.85, 0.55 + (asia_drift_bps / 500.0))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "asia_drift_us_continuation",
                "asia_drift_bps": round(asia_drift_bps, 2),
                "ema_trend": round(ema_trend, 2),
                "close": bar.close,
                "fear_greed": fear_greed,
                "hour": hour
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index