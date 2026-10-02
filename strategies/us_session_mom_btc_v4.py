from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcSessionMomentum(Strategy):
    METADATA = {
        "name": "BtcSessionMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 10
        self.ema_fast_len = 8
        self.ema_slow_len = 21
        self.min_return_threshold = 0.0055  # 0.55% price movement over lookback
        self.last_trade_day = None
        self.cooldown_until_bar = 0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.ema_slow_len + 5)
        if len(closes) < self.lookback + self.ema_slow_len:
            return None

        dt = ctx.bar.timestamp
        current_day = dt.date()
        hour = dt.hour

        # Force session close at or after 21:00 UTC
        if hour >= 21:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_eod",
                        "hour": hour,
                        "close_price": ctx.bar.close,
                    }
                )
            return None

        # Session trading window: 13:00 to 17:00 UTC for entries
        if hour < 13 or hour > 17:
            return None

        # Only 1 trade entry per calendar day and honor bar cooldown
        if self.last_trade_day == current_day or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Do not enter if position already active
        if ctx.has_position():
            return None

        # Calculate momentum over lookback
        curr_close = closes[-1]
        prior_close = closes[-self.lookback]
        roc = (curr_close - prior_close) / prior_close

        ema_fast = self._ema(closes, self.ema_fast_len)
        ema_slow = self._ema(closes, self.ema_slow_len)
        if ema_fast is None or ema_slow is None:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        atr_pct = (atr / curr_close) if (atr and curr_close > 0) else 0.01

        # Check market regime to avoid crisis / extreme meltdown entries
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Long continuation setup: Strong pre-session positive momentum + Fast EMA above Slow EMA
        if roc >= self.min_return_threshold and ema_fast > ema_slow:
            conf = min(0.9, max(0.55, 0.55 + (roc / 0.02) * 0.25))
            self.last_trade_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 6
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bull_continuation",
                    "roc_pct": round(roc * 100, 3),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "atr_pct": round(atr_pct * 100, 3),
                    "hour": hour,
                }
            )

        # Short continuation setup: Strong pre-session negative momentum + Fast EMA below Slow EMA
        if roc <= -self.min_return_threshold and ema_fast < ema_slow:
            conf = min(0.9, max(0.55, 0.55 + (abs(roc) / 0.02) * 0.25))
            self.last_trade_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 6
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bear_continuation",
                    "roc_pct": round(roc * 100, 3),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "atr_pct": round(atr_pct * 100, 3),
                    "hour": hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce extra 4 bars cooldown after closing to prevent immediate re-entry
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 4)