from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar_idx = -1
        self.last_exit_bar = -1
        self.cooldown_bars = 4

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Session Exit: Close positions before the US session ends (UTC 21)
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_close",
                        "hour": current_hour,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Mandatory Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # One trade per day restriction & entry strictly at UTC 15 (after 13:00 and 14:00 bars close)
        if current_day == self.last_traded_day or current_hour != 15:
            return None

        # Crisis / Meltdown safety guard
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(35)
        lows = ctx.lows(35)
        atr_val = self._atr(highs, lows, closes, period=14)
        if atr_val is None or atr_val <= 0:
            return None

        # Two-hour opening window: 13:00 open was ctx.opens(3)[-2], 14:59 close is ctx.bar.close
        opens = ctx.opens(5)
        window_open = opens[-2]
        window_close = ctx.bar.close
        move = window_close - window_open

        # Dynamic median 2-bar move over the past 24 hours (12 non-overlapping 2-bar segments)
        recent_ranges = []
        for i in range(2, 26, 2):
            recent_ranges.append(abs(closes[-i] - closes[-i-2]))
        median_range = statistics.median(recent_ranges) if recent_ranges else atr_val

        # Opening move must exceed recent median range, but not be overextended (> 2.8 * ATR)
        abs_move = abs(move)
        if abs_move < median_range * 1.05 or abs_move > atr_val * 2.8:
            return None

        # Overall trend alignment check (24h SMA)
        sma24 = sum(closes[-24:]) / 24.0

        if move > 0 and window_close >= sma24:
            self.last_traded_day = current_day
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move": move,
                    "median_range": median_range,
                    "atr": atr_val,
                    "sma24": sma24,
                    "price": window_close
                }
            )

        if move < 0 and window_close <= sma24:
            self.last_traded_day = current_day
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move": move,
                    "median_range": median_range,
                    "atr": atr_val,
                    "sma24": sma24,
                    "price": window_close
                }
            )

        return None