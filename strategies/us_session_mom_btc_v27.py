from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # Max 6h hold (intra-session)
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.last_exit_bar = -100
        self.cooldown_bars = 4

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
        closes = ctx.closes(25)
        if len(closes) < 25:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_date = bar_time.date()

        # Hard session close: Flatten position at or after 21 UTC
        if current_hour >= 21:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "bar_index": ctx.bar_index}
                )
            return None

        # Intraday hold: If already in position, let it ride until 21 UTC or engine SL/TP
        if ctx.has_position():
            return None

        # Check cooldown and one-trade-per-day restriction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.last_traded_date == current_date:
            return None

        # Entry window: Primary opening drive between 13:00 and 15:00 UTC
        if not (13 <= current_hour <= 15):
            return None

        # Market stress filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Measure 12-bar pre-session return momentum
        ret_12 = (closes[-1] - closes[-13]) / closes[-13]

        # Volatility normalization via ATR (14)
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None

        # Require price move over 12 bars to exceed 1.25x recent ATR
        atr_pct = atr / closes[-1]
        threshold = max(0.0075, atr_pct * 1.25)

        # Volatility-adjusted stop-loss (1.75 * ATR in bps)
        dyn_sl_bps = min(350.0, max(140.0, (atr * 1.75 / closes[-1]) * 10000.0))
        dyn_tp_bps = dyn_sl_bps * 1.8

        # Long continuation
        if ret_12 > threshold:
            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_long_momentum",
                    "ret_12": round(ret_12, 4),
                    "threshold": round(threshold, 4),
                    "atr_bps": round(atr_pct * 10000.0, 1),
                    "hour": current_hour
                }
            )

        # Short continuation
        if ret_12 < -threshold:
            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_short_momentum",
                    "ret_12": round(ret_12, 4),
                    "threshold": round(threshold, 4),
                    "atr_bps": round(atr_pct * 10000.0, 1),
                    "hour": current_hour
                }
            )

        return None