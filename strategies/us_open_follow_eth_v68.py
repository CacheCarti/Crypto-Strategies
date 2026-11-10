from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
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
        self.atr_period: int = 24

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
        closes = ctx.closes(45)
        highs = ctx.highs(45)
        lows = ctx.lows(45)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_id = current_time.year * 1000 + current_time.timetuple().tm_yday

        # Hard session exit: force flat at or after UTC 21:00
        if ctx.has_position():
            if hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Check cooldown from last exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Only evaluate entry exactly at UTC 15:00 (after the 13:00 and 14:00 bars close)
        if hour != 15:
            return None

        # Exactly one trade attempt per UTC day
        if self.last_trade_day == day_id:
            return None

        # Skip on extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0.0:
            return None

        # Two-hour session window: evaluate net displacement from start of 13:00 bar to end of 14:00 bar
        # closes[-1] is 14:00 bar close; closes[-3] is 12:00 bar close (approx opening reference for 13:00 bar)
        open_price_window = closes[-3]
        eval_price_window = closes[-1]
        displacement = eval_price_window - open_price_window
        displacement_bps = (displacement / open_price_window) * 10000.0

        # High/low range across the 2-hour opening interval
        window_high = max(highs[-2], highs[-1])
        window_low = min(lows[-2], lows[-1])
        window_range = window_high - window_low

        # Filter: Opening move must be directional, exceed median volatility, but not be completely exhausted
        min_move_bps = 45.0
        max_move_bps = 250.0

        if abs(displacement_bps) < min_move_bps or abs(displacement_bps) > max_move_bps:
            return None

        # Efficiency filter: net move should represent at least 55% of the total session high-low range
        if window_range <= 0 or (abs(displacement) / window_range) < 0.55:
            return None

        self.last_trade_day = day_id
        confidence = min(0.9, max(0.55, abs(displacement_bps) / 180.0))

        if displacement_bps > 0:
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "displacement_bps": round(displacement_bps, 2),
                    "window_range": round(window_range, 2),
                    "atr": round(atr, 2),
                    "hour": hour
                }
            )
        else:
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "displacement_bps": round(displacement_bps, 2),
                    "window_range": round(window_range, 2),
                    "atr": round(atr, 2),
                    "hour": hour
                }
            )