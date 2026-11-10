from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

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
        self.last_traded_date = None
        self.lookback_atr = 24
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(self.lookback_atr + 5)
        if len(closes) < self.lookback_atr + 2:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour

        # Hard timed exit at or after UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timed_exit", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # One trade per day limit
        if self.last_traded_date == current_date:
            return None

        # Trigger exactly at the close of UTC 15 (start of continuation window: 15:00-15:59)
        if hour != 15:
            return None

        # Avoid extreme crisis conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        highs = ctx.highs(self.lookback_atr + 5)
        lows = ctx.lows(self.lookback_atr + 5)

        # 2-bar session move (covering hours 13 and 14 up to current 15 bar open)
        # closes[-1] is bar 15 close, closes[-3] is bar 13 open/close proxy
        session_open = ctx.opens(3)[0]
        session_close = closes[-1]
        session_move = session_close - session_open
        session_move_pct = abs(session_move) / session_open

        # Compute rolling median 2-bar displacement as threshold
        disp_history = []
        c_hist = ctx.closes(28)
        o_hist = ctx.opens(28)
        for i in range(2, len(c_hist)):
            disp = abs(c_hist[i] - o_hist[i - 1]) / o_hist[i - 1]
            disp_history.append(disp)

        median_disp = statistics.median(disp_history) if len(disp_history) >= 10 else 0.007

        # Require meaningful expansion beyond median displacement, but avoid overextension (> 3.5%)
        if session_move_pct < median_disp * 1.15 or session_move_pct > 0.035:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        atr_bps = (atr / session_close) * 10000.0
        # Dynamic structural volatility stops
        dynamic_sl_bps = max(140.0, min(300.0, atr_bps * 1.6))
        dynamic_tp_bps = max(220.0, min(480.0, dynamic_sl_bps * 1.6))

        direction = "long" if session_move > 0 else "short"
        confidence = min(0.85, 0.55 + (session_move_pct / 0.035) * 0.25)

        self.last_traded_date = current_date

        return ctx.signal(
            direction,
            confidence=round(confidence, 2),
            stop_loss_bps=round(dynamic_sl_bps, 1),
            take_profit_bps=round(dynamic_tp_bps, 1),
            horizon_seconds=21600,
            metadata={
                "reason": f"us_open_{direction}_continuation",
                "session_move_pct": round(session_move_pct * 100, 3),
                "median_disp_pct": round(median_disp * 100, 3),
                "atr_bps": round(atr_bps, 1),
                "hour": hour,
            }
        )