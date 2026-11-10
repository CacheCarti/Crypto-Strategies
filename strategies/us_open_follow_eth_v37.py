from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class USOpenMomentumContinuation(Strategy):
    METADATA = {
        "name": "US Open Momentum Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.entry_bar_idx = -100

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
        closes = ctx.closes(48)
        if len(closes) < 48:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour

        # Timed exit: flatten if held into or past UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timeout", "hour": hour, "date": str(current_date)}
                )
            return None

        # Entry window: strictly trigger at UTC 15 (after 13:00 and 14:00 bars close)
        if hour != 15:
            return None

        # Ensure strictly one trade per day
        if self.last_traded_date == current_date:
            return None

        # Calculate opening session net move (13:00 open to 15:00 open/14:00 close)
        # Bar at index -1 is 14:00 bar, bar at -2 is 13:00 bar
        opens = ctx.opens(48)
        highs = ctx.highs(48)
        lows = ctx.lows(48)

        open_13 = opens[-2]
        close_14 = closes[-1]
        session_net_move = close_14 - open_13
        session_range = max(highs[-2], highs[-1]) - min(lows[-2], lows[-1])

        # Median range benchmark over the past 20 2-bar intervals
        prior_ranges = []
        for i in range(4, 44, 2):
            h_win = max(highs[-i], highs[-i + 1])
            l_win = min(lows[-i], lows[-i + 1])
            prior_ranges.append(h_win - l_win)

        if not prior_ranges:
            return None

        med_range = statistics.median(prior_ranges)

        # Volatility via ATR for dynamic stop placement
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Expansion check: opening range must convincingly expand past recent median
        if session_range < 1.25 * med_range:
            return None

        # Directional conviction: net displacement must capture most of the session's range
        # to ensure it's not a choppy two-sided range expansion
        if session_range == 0:
            return None
        displacement_ratio = abs(session_net_move) / session_range

        if displacement_ratio < 0.65:
            return None

        # Avoid overextended moves that are prime for exhaustion reversals (> 3.2 ATR)
        if abs(session_net_move) > 3.2 * atr:
            return None

        current_price = ctx.bar.close
        sl_bps = max(100.0, min(350.0, (1.6 * atr / current_price) * 10000.0))
        tp_bps = sl_bps * 1.8

        self.last_traded_date = current_date
        self.entry_bar_idx = ctx.bar_index

        if session_net_move > 0:
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.5 + displacement_ratio * 0.4),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "displacement_ratio": round(displacement_ratio, 3),
                    "session_range": round(session_range, 2),
                    "med_range": round(med_range, 2),
                    "atr": round(atr, 2),
                    "sl_bps": round(sl_bps, 1),
                }
            )
        elif session_net_move < 0:
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.5 + displacement_ratio * 0.4),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "displacement_ratio": round(displacement_ratio, 3),
                    "session_range": round(session_range, 2),
                    "med_range": round(med_range, 2),
                    "atr": round(atr, 2),
                    "sl_bps": round(sl_bps, 1),
                }
            )

        return None