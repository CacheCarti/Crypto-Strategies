from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum Event",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max intraday hold ~6 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.last_trade_day = -1
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 15)
        if len(closes) < self.lookback + 15:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Hard session close: liquidate any existing position at or after UTC 20
        if ctx.has_position():
            if current_hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc20", "hour": current_hour, "bar_index": ctx.bar_index}
                )
            return None

        # Entry window strictly at US Open impulse (UTC 13 or 14)
        if current_hour not in (13, 14):
            return None

        # Enforce exactly one setup per calendar day & cooldown after manual exit
        if current_day == self.last_trade_day:
            return None
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety: avoid entering during extreme market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Pre-session 12-bar displacement vs ATR volatility threshold
        highs = ctx.highs(self.lookback + 15)
        lows = ctx.lows(self.lookback + 15)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        p_now = closes[-1]
        p_prev = closes[-1 - self.lookback]
        displacement = p_now - p_prev
        disp_bps = (displacement / p_prev) * 10000.0

        # Displace must exceed at least 1.25x 14-bar ATR to confirm clear pre-market impulse
        min_disp_required = 1.25 * atr
        if abs(displacement) < min_disp_required:
            return None

        self.last_trade_day = current_day

        if displacement > 0:
            conf = min(0.90, max(0.55, 0.55 + (disp_bps / 500.0) * 0.25))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "disp_bps": round(disp_bps, 2),
                    "atr": round(atr, 2),
                    "hour": current_hour,
                    "crisis_score": crisis_score,
                }
            )
        else:
            conf = min(0.90, max(0.55, 0.55 + (abs(disp_bps) / 500.0) * 0.25))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "disp_bps": round(disp_bps, 2),
                    "atr": round(atr, 2),
                    "hour": current_hour,
                    "crisis_score": crisis_score,
                }
            )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index