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
        self.last_traded_date = None
        self.entry_bar_index = -1
        self.min_cooldown_bars = 16

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 25:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour

        # Session close rule: Liquidate any open position by 21:00 UTC
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={
                        "reason": "us_session_close",
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Entry window: strictly trigger at US session open window (13:00 to 14:00 UTC)
        if hour not in (13, 14):
            return None

        # Rate limiting: strictly one setup per day and enforce bar cooldown
        if self.last_traded_date == current_date:
            return None

        if (ctx.bar_index - self.entry_bar_index) < self.min_cooldown_bars:
            return None

        # Market regime filter: avoid entering during extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # 12-bar pre-session price displacement
        displacement = (closes[-1] - closes[-13]) / closes[-13]

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or closes[-1] == 0:
            return None

        atr_pct = atr / closes[-1]
        threshold = max(0.0075, atr_pct * 0.8)

        # Baseline trend filter (24-bar simple moving average)
        sma24 = sum(closes[-24:]) / 24.0

        if displacement > threshold and closes[-1] > sma24:
            self.last_traded_date = current_date
            self.entry_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(1.0, 0.5 + abs(displacement) * 20),
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "displacement": displacement,
                    "threshold": threshold,
                    "sma24": sma24,
                    "atr": atr,
                    "hour": hour
                }
            )

        if displacement < -threshold and closes[-1] < sma24:
            self.last_traded_date = current_date
            self.entry_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(1.0, 0.5 + abs(displacement) * 20),
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "displacement": displacement,
                    "threshold": threshold,
                    "sma24": sma24,
                    "atr": atr,
                    "hour": hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass