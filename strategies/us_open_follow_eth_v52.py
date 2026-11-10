from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "UsOpenFollowEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day: Optional[int] = None
        self.last_exit_bar: int = -999
        self.cooldown_bars: int = 4

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _median_range(self, highs, lows, period=24):
        if len(highs) < period:
            return None
        ranges = sorted([h - l for h, l in zip(highs[-period:], lows[-period:])])
        mid = period // 2
        return (ranges[mid - 1] + ranges[mid]) / 2.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 40:
            return None

        # Time management
        bar_dt = ctx.bar.timestamp
        current_day = bar_dt.toordinal()
        current_hour = bar_dt.hour

        # Force intraday exit by UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Mandatory post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # One trade per day limit
        if self.last_trade_day == current_day:
            return None

        # Session timing: Enter specifically at UTC 15:00 after observing 13:00 and 14:00 bars
        if current_hour != 15:
            return None

        # Regime gate: Prior failures in family indicate avoiding crisis/choppy breakdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")

        # 2-hour US opening window moves (from UTC 13:00 open to UTC 15:00 close)
        open_13 = ctx.opens(3)[-2]
        close_15 = ctx.bar.close
        move = close_15 - open_13
        abs_move = abs(move)

        atr_val = self._atr(highs, lows, closes, 14)
        med_range = self._median_range(highs, lows, 24)
        if atr_val is None or med_range is None or med_range == 0:
            return None

        # Move must exceed recent median bar range to confirm clear direction, but not overextended (> 3.5 ATR)
        if abs_move < (1.1 * med_range) or abs_move > (3.5 * atr_val):
            return None

        # Evaluate directional setups aligned with macro trend
        is_bullish = move > 0 and (trend_regime != "bear")
        is_bearish = move < 0 and (trend_regime != "bull")

        if is_bullish:
            self.last_trade_day = current_day
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move": round(move, 2),
                    "med_range": round(med_range, 2),
                    "atr": round(atr_val, 2),
                    "regime": regime
                }
            )

        if is_bearish:
            self.last_trade_day = current_day
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move": round(move, 2),
                    "med_range": round(med_range, 2),
                    "atr": round(atr_val, 2),
                    "regime": regime
                }
            )

        return None