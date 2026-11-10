from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max intraday hold ~6 hours (UTC 15 to UTC 21)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 24
        self.last_traded_date = None
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=24) -> Optional[float]:
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
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        opens = ctx.opens(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        current_date = bar_time.date()
        hour = bar_time.hour

        # Hard intraday cutoff: Flat at or after UTC 21
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "close": ctx.bar.close}
                )
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # One trade per calendar day, executed strictly at UTC 15:00 bar close
        # observing the 2-hour window of UTC 13:00 and 14:00.
        if hour != 15 or self.last_traded_date == current_date:
            return None

        # Bar -2 is 13:00 bar, Bar -1 is 14:00 bar
        open_13 = opens[-2]
        close_14 = closes[-1]
        move_pct = (close_14 - open_13) / open_13

        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None or atr_val == 0:
            return None

        current_price = ctx.bar.close
        atr_pct = atr_val / current_price

        # The two-hour move must be distinct (> 0.7 ATR) but not overextended (< 2.2 ATR)
        move_magnitude = abs(move_pct)
        if move_magnitude < (0.7 * atr_pct) or move_magnitude > (2.2 * atr_pct):
            return None

        # Check internal consistency: both 13:00 and 14:00 bars should agree or push toward the move
        bar13_dir = closes[-2] - opens[-2]
        bar14_dir = closes[-1] - opens[-1]

        # Market regime filter from context (do not trade in MELTDOWN / CRISIS regimes)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long continuation setup
        if move_pct > 0 and bar14_dir > 0:
            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_pct": round(move_pct * 100, 3),
                    "atr_pct": round(atr_pct * 100, 3),
                    "hour": hour,
                    "regime": regime
                }
            )

        # Short continuation setup
        if move_pct < 0 and bar14_dir < 0:
            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_pct": round(move_pct * 100, 3),
                    "atr_pct": round(atr_pct * 100, 3),
                    "hour": hour,
                    "regime": regime
                }
            )

        return None