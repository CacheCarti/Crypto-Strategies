from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionMomentumBTC(Strategy):
    METADATA = {
        "name": "USSessionMomentumBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar_idx = -1
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
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Exit all positions before UTC 21 or if hold limit reached
        if ctx.has_position():
            if current_hour >= 20 or current_hour < 13:
                pos_dir = ctx.position_direction()
                return ctx.signal("flat", confidence=1.0, metadata={
                    "reason": "session_close_utc20",
                    "current_hour": current_hour,
                    "pos_dir": pos_dir,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard: must wait N bars since last entry attempt
        if ctx.bar_index - self.entry_bar_idx < self.cooldown_bars:
            return None

        # Trade only once per day during session entry window (UTC 13 or 14)
        if current_day == self.last_traded_day:
            return None

        if current_hour not in (13, 14):
            return None

        # Market regime filter: avoid entering during extreme panic
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Compute 12-bar pre-session momentum and ATR
        lookback = 12
        p_now = closes[-1]
        p_prev = closes[-lookback - 1]
        ret_12 = (p_now - p_prev) / p_prev

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None

        # Normalized momentum: required move relative to typical volatility
        norm_mom = (p_now - p_prev) / atr
        threshold = 1.25

        # Check for clean directional momentum
        if norm_mom > threshold and ret_12 > 0.003:
            self.last_traded_day = current_day
            self.entry_bar_idx = ctx.bar_index
            conf = min(0.9, 0.5 + abs(norm_mom) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "norm_mom": round(norm_mom, 2),
                    "ret_12_bps": round(ret_12 * 10000, 1),
                    "atr": round(atr, 2),
                    "hour": current_hour
                }
            )

        if norm_mom < -threshold and ret_12 < -0.003:
            self.last_traded_day = current_day
            self.entry_bar_idx = ctx.bar_index
            conf = min(0.9, 0.5 + abs(norm_mom) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "norm_mom": round(norm_mom, 2),
                    "ret_12_bps": round(ret_12 * 10000, 1),
                    "atr": round(atr, 2),
                    "hour": current_hour
                }
            )

        return None