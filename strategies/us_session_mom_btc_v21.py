from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.atr_period = 14
        self.min_move_mult = 1.0
        self.session_start_hour = 13
        self.session_end_hour = 21
        self.last_traded_day = -1
        self.last_exit_bar = -999
        self.cooldown_bars = 4

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.lookback + self.atr_period + 5)
        if len(closes) < self.lookback + self.atr_period:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.timetuple().tm_yday

        # Mandatory session exit: close before UTC 21 if position is still open
        if ctx.has_position():
            if current_hour >= self.session_end_hour or current_hour < self.session_start_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "session_close_cutoff",
                        "hour": current_hour,
                        "bar_index": ctx.bar_index
                    }
                )
            return None

        # Cooldown & daily trade constraint check
        if current_day == self.last_traded_day:
            return None
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Only trigger momentum entry within the first 3 hours of the US session
        if not (self.session_start_hour <= current_hour <= self.session_start_hour + 2):
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.75:
            return None

        highs = ctx.highs(self.lookback + self.atr_period + 5)
        lows = ctx.lows(self.lookback + self.atr_period + 5)

        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None or atr_val <= 0:
            return None

        current_price = closes[-1]
        prior_price = closes[-self.lookback]
        net_move = current_price - prior_price
        threshold = atr_val * self.min_move_mult

        # Entry logic: sustained directional expansion over the prior 12 hours
        if net_move > threshold:
            self.last_traded_day = current_day
            conf = min(0.9, max(0.5, 0.5 + (net_move / (threshold * 3.0)) * 0.4))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_continuation_long",
                    "net_move": net_move,
                    "threshold": threshold,
                    "atr": atr_val,
                    "hour": current_hour
                }
            )

        if net_move < -threshold:
            self.last_traded_day = current_day
            conf = min(0.9, max(0.5, 0.5 + (abs(net_move) / (threshold * 3.0)) * 0.4))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_continuation_short",
                    "net_move": net_move,
                    "threshold": threshold,
                    "atr": atr_val,
                    "hour": current_hour
                }
            )

        return None