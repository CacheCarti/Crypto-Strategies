from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session BTC Momentum Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour = 13
        self.exit_hour = 21
        self.lookback = 12
        self.min_ret_bps = 50.0
        self.last_trade_day = -1
        self.last_exit_bar = -100
        self.cooldown_bars = 4

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Hard intraday cutoff: flatten before or at UTC 21
        if ctx.has_position():
            if current_hour >= self.exit_hour or current_hour < self.entry_hour:
                pos = ctx.position or {}
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_cutoff",
                        "hour": current_hour,
                        "entry_price": pos.get("entry_price", ctx.bar.close),
                        "close_price": ctx.bar.close,
                    }
                )
            return None

        # One trade per day, cooldown check, and strictly trigger at session open (13:00 - 14:00 UTC)
        if current_day == self.last_trade_day:
            return None
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if current_hour != self.entry_hour:
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.6 or regime == "MELTDOWN":
            return None

        # 12-bar pre-session price direction
        p_now = closes[-1]
        p_prev = closes[-self.lookback]
        ret_bps = ((p_now - p_prev) / p_prev) * 10000.0

        # Filter out flat/noisy pre-sessions
        if abs(ret_bps) < self.min_ret_bps:
            return None

        # Volatility check: require realistic room to run
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None
        atr_bps = (atr / p_now) * 10000.0
        if atr_bps < 30.0:
            return None

        # Moving average filter for trend confirmation
        sma_fast = sum(closes[-8:]) / 8.0
        sma_slow = sum(closes[-24:]) / 24.0

        # Dynamic confidence based on magnitude of the pre-session move
        conf = min(0.9, max(0.55, 0.5 + abs(ret_bps) / 400.0))

        if ret_bps > self.min_ret_bps and sma_fast > sma_slow and p_now > sma_fast:
            self.last_trade_day = current_day
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bull_continuation",
                    "ret_bps": round(ret_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "sma_fast": round(sma_fast, 2),
                    "sma_slow": round(sma_slow, 2),
                    "hour": current_hour,
                }
            )

        if ret_bps < -self.min_ret_bps and sma_fast < sma_slow and p_now < sma_fast:
            self.last_trade_day = current_day
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bear_continuation",
                    "ret_bps": round(ret_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "sma_fast": round(sma_fast, 2),
                    "sma_slow": round(sma_slow, 2),
                    "hour": current_hour,
                }
            )

        return None