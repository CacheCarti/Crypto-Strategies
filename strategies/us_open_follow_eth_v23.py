from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
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
        self.cooldown_until_bar = ctx.bar_index + 3

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 25:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour

        # Exit intraday before session close
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Trigger check at UTC 15 (after 13:00 and 14:00 bars close)
        if hour != 15:
            return None

        if self.last_traded_date == current_date:
            return None

        # Filter out extreme regime crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Measure move over UTC 13:00 to 15:00 (the 2-hour opening window)
        open_price_13 = ctx.opens(3)[-2]
        close_price_14 = ctx.bar.close
        move = close_price_14 - open_price_13
        abs_move = abs(move)

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        ranges = [highs[-i] - lows[-i] for i in range(1, 15)]
        median_range = statistics.median(ranges)

        # Opening move must be decisive (> 1.25x median hourly range)
        # but not overextended (< 3.0x ATR)
        if abs_move < median_range * 1.25 or abs_move > atr * 3.0:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        confidence = 0.70

        if move > 0 and trend_regime != "bear":
            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move": move,
                    "abs_move": abs_move,
                    "atr": atr,
                    "median_range": median_range,
                    "hour": hour
                }
            )
        elif move < 0 and trend_regime != "bull":
            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move": move,
                    "abs_move": abs_move,
                    "atr": atr,
                    "median_range": median_range,
                    "hour": hour
                }
            )

        return None