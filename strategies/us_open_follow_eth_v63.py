from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours (from 15:00 UTC to 21:00 UTC)
        "warmup_bars": 48,
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
        # Enforce an explicit multi-bar cooldown after position closure
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        highs = ctx.highs(48)
        lows = ctx.lows(48)
        opens = ctx.opens(48)

        if len(closes) < 48:
            return None

        current_dt = ctx.bar.timestamp
        current_date = current_dt.date()
        hour = current_dt.hour

        # Mandatory session exit: close by UTC 21 regardless
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                pos_dir = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "pos_dir": pos_dir}
                )
            return None

        # Respect cooldown and daily limit
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_date == current_date:
            return None

        # Entry evaluated exclusively right after the first two US-session hours (UTC 13 and 14)
        # Bar with timestamp hour == 15 represents the opening of the continuation window
        if hour != 15:
            return None

        # Opening move: from open of 13:00 bar (index -3) to close of 14:00 bar (index -2)
        # Note: closes[-1] is the bar ending/at 15:00, closes[-2] is 14:00, opens[-3] is open of 13:00
        open_13 = opens[-3]
        close_14 = closes[-2]
        move = close_14 - open_13
        move_bps = (move / open_13) * 10000.0
        abs_move_bps = abs(move_bps)

        # Baseline volatility check: require move to exceed recent 24-bar median 2-bar displacement
        recent_ranges = [abs(closes[i] - opens[i - 1]) / opens[i - 1] * 10000.0 for i in range(-24, 0)]
        median_range_bps = statistics.median(recent_ranges) if recent_ranges else 40.0

        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_bps = (atr / closes[-1]) * 10000.0

        # Avoid overextended moves (capping at 2.5x ATR to avoid buying the top / selling the bottom)
        # and ensure impulse is meaningful (> max of median_range and 35 bps)
        min_move_bps = max(35.0, median_range_bps * 1.1)
        max_move_bps = atr_bps * 2.5

        if abs_move_bps < min_move_bps or abs_move_bps > max_move_bps:
            return None

        # Regime & Crisis safety filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Alignment with broader trend (24-hour EMA)
        ema_24 = sum(closes[-24:]) / 24.0

        if move_bps > 0 and closes[-1] >= ema_24:
            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_bps": round(move_bps, 2),
                    "median_range_bps": round(median_range_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "hour": hour
                }
            )

        elif move_bps < 0 and closes[-1] <= ema_24:
            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_bps": round(move_bps, 2),
                    "median_range_bps": round(median_range_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "hour": hour
                }
            )

        return None