from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 21600,  # Max 6 hours
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
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
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        highs = ctx.highs(48)
        lows = ctx.lows(48)

        if len(closes) < 48:
            return None

        ts = ctx.bar.timestamp
        current_hour = ts.hour
        current_day = ts.toordinal()

        # Hard timed exit at UTC 21 (end of US session)
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "close": ctx.bar.close}
                )
            return None

        # Entry window strictly at UTC 15 (evaluates the 13:00 - 15:00 opening drive)
        if current_hour != 15:
            return None

        # One trade per calendar day & cooldown check
        if current_day == self.last_traded_day or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Structural opening move calculation (13:00 to 15:00 UTC)
        # Bar at index -1 represents 14:00->15:00, index -2 represents 13:00->14:00
        open_price_13 = ctx.opens(3)[0]
        close_price_15 = closes[-1]
        session_net_move = close_price_15 - open_price_13
        abs_net_move = abs(session_net_move)

        # Volatility baseline: Median of 1-hour ranges over past 24 hours
        ranges_24h = [highs[i] - lows[i] for i in range(-24, 0)]
        median_range_1h = statistics.median(ranges_24h)
        if median_range_1h <= 0:
            return None

        # Dynamic volatility stop calculation via ATR
        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val is None or atr_val <= 0:
            return None

        # Risk parameters dynamic to ETH volatility
        sl_bps = max(150.0, min(300.0, (atr_val * 1.5 / close_price_15) * 10000.0))
        tp_bps = sl_bps * 1.6

        # Move requirement: Opening move must be at least 1.4x the median 1h range
        # Avoid overextended moves (> 4.5x median range)
        ratio = abs_net_move / median_range_1h
        if ratio < 1.4 or ratio > 4.5:
            return None

        # Avoid extreme high-crisis market breakdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.8:
            return None

        self.last_traded_day = current_day

        if session_net_move > 0:
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.55 + (ratio * 0.08)),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_ratio": ratio,
                    "net_move": session_net_move,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps,
                }
            )
        else:
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.55 + (ratio * 0.08)),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_ratio": ratio,
                    "net_move": session_net_move,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps,
                }
            )