from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours (15:00 UTC to 21:00 UTC)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.open_13_price = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour

        # Check for mandatory daily close by 21:00 UTC
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "close": ctx.bar.close}
                )
            return None

        # Track the price at the beginning of the US session window (13:00 UTC bar open)
        if current_hour == 13:
            self.open_13_price = ctx.bar.open

        # Evaluate continuation only right after the 2-hour window finishes (15:00 UTC)
        if current_hour != 15:
            return None

        # Max one trade per day
        if self.last_traded_day == current_day:
            return None

        if self.open_13_price is None or self.open_13_price <= 0:
            return None

        highs = ctx.highs(35)
        lows = ctx.lows(35)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Opening move across 13:00-15:00 UTC
        current_close = ctx.bar.close
        move = current_close - self.open_13_price
        move_bps = (move / self.open_13_price) * 10000.0

        # Calculate recent median 2-bar range to ensure opening impulse exceeds recent median range
        two_bar_ranges = []
        for i in range(14, len(closes)):
            two_bar_ranges.append(abs(closes[i] - closes[i - 2]))
        two_bar_ranges.sort()
        median_range = two_bar_ranges[len(two_bar_ranges) // 2] if two_bar_ranges else atr

        # Impulsive requirement: move must be decisive and exceed median range
        abs_move = abs(move)
        if abs_move < median_range * 1.1 or abs_move < 0.6 * atr:
            return None

        # Avoid overextension (parabolic exhaustion rejection)
        if abs_move > 2.8 * atr:
            return None

        # Trend alignment: 24-period SMA for macro regime context
        sma24 = sum(closes[-24:]) / 24.0

        # Long entry: decisive positive impulse, not overextended, aligned with macro trend
        if move > 0 and current_close > sma24 and move_bps >= 35.0:
            self.last_traded_day = current_day
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_bps": round(move_bps, 1),
                    "atr": round(atr, 2),
                    "median_range": round(median_range, 2),
                    "sma24": round(sma24, 2),
                    "hour": current_hour
                }
            )

        # Short entry: decisive negative impulse, not overextended, aligned with macro trend
        if move < 0 and current_close < sma24 and move_bps <= -35.0:
            self.last_traded_day = current_day
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_bps": round(move_bps, 1),
                    "atr": round(atr, 2),
                    "median_range": round(median_range, 2),
                    "sma24": round(sma24, 2),
                    "hour": current_hour
                }
            )

        return None