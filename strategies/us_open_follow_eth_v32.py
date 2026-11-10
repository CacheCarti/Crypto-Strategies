from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours (UTC 15 to 21)
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.entry_bar_idx = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown after position exit
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        if len(closes) < 48:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Exit condition: flatten by UTC 21 or later if holding a position
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                pos = ctx.position or {}
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_utc21",
                        "hour": current_hour,
                        "entry_price": pos.get("entry_price", ctx.bar.close),
                        "close_price": ctx.bar.close
                    }
                )
            return None

        # Check cooldown and one-trade-per-day constraint
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_trade_day == current_day:
            return None

        # Entry window: trigger precisely at UTC 15 (after observing 13:00 and 14:00 bars)
        if current_hour != 15:
            return None

        # Regime gate to avoid dominant failure modes: filter out extreme crisis / volatile panics
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            return None

        highs = ctx.highs(48)
        lows = ctx.lows(48)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # 2-hour opening move: price at start of UTC 13 (closes[-3]) to close of UTC 14 (closes[-1])
        open_price_13 = closes[-3]
        eval_price_15 = closes[-1]
        move = eval_price_15 - open_price_13
        abs_move = abs(move)

        # Baseline: recent median 2-bar absolute range
        recent_2bar_ranges = [abs(closes[i] - closes[i - 2]) for i in range(2, len(closes))]
        median_range = statistics.median(recent_2bar_ranges) if recent_2bar_ranges else atr

        # Opening move must exceed median range, but not be completely overextended (> 3.5 * ATR)
        if abs_move < median_range * 1.05 or abs_move > atr * 3.5:
            return None

        # Determine directional continuation
        trend_regime = ctx.market.get("trend_regime", "neutral")
        sl_bps = self.METADATA["declared_sl_bps"]
        tp_bps = self.METADATA["declared_tp_bps"]

        if move > 0:
            # Avoid long entries into structural bear regimes
            if trend_regime == "bear":
                return None
            self.last_trade_day = current_day
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move": round(move, 2),
                    "abs_move": round(abs_move, 2),
                    "median_range": round(median_range, 2),
                    "atr": round(atr, 2),
                    "trend_regime": trend_regime,
                }
            )
        else:
            # Avoid short entries into structural bull regimes
            if trend_regime == "bull":
                return None
            self.last_trade_day = current_day
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move": round(move, 2),
                    "abs_move": round(abs_move, 2),
                    "median_range": round(median_range, 2),
                    "atr": round(atr, 2),
                    "trend_regime": trend_regime,
                }
            )