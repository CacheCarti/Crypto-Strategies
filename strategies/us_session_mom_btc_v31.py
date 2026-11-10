from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # up to 6 hours intra-session
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.last_exit_bar: int = -50
        self.cooldown_bars: int = 4
        self.lookback: int = 12
        self.mom_threshold_pct: float = 0.65  # 0.65% pre-US momentum threshold

    def _atr(self, highs, lows, closes, period=14) -> Optional[float]:
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
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 15:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.timetuple().tm_yday

        # Session forced exit: Flatten before US session close (UTC 21)
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_flat", "hour": current_hour, "bar_index": ctx.bar_index}
                )
            return None

        # Entry window: Primary entries trigger during session open & early continuation (UTC 13-15)
        if current_hour not in (13, 14, 15):
            return None

        # One trade per calendar day & enforce post-exit cooldown
        if self.last_day_traded == current_day if hasattr(self, 'last_day_traded') else False:
            return None
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime & macro gating
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Check 12-bar pre-session price displacement
        p_now = closes[-1]
        p_prev = closes[-self.lookback - 1]
        mom_pct = ((p_now - p_prev) / p_prev) * 100.0

        # Dynamic volatility check: ensure sufficient movement
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None:
            return None

        # Check perp funding crowding to prevent chasing over-levered expansions
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Bullish momentum continuation
        if mom_pct > self.mom_threshold_pct and funding < 0.0003:
            self.last_day_traded = current_day
            conf = min(0.9, 0.55 + abs(mom_pct) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_12h_pct": round(mom_pct, 3),
                    "atr": round(atr, 2),
                    "funding": funding,
                    "hour": current_hour,
                }
            )

        # Bearish momentum continuation
        if mom_pct < -self.mom_threshold_pct and funding > -0.0003:
            self.last_day_traded = current_day
            conf = min(0.9, 0.55 + abs(mom_pct) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_12h_pct": round(mom_pct, 3),
                    "atr": round(atr, 2),
                    "funding": funding,
                    "hour": current_hour,
                }
            )

        return None