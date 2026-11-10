from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours (intraday US session hold)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0
        self.lookback = 12

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 2:
            return None

        dt = ctx.bar.timestamp
        current_hour = dt.hour
        current_day = dt.toordinal()

        # Regime & Crisis Filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.5 or regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "crisis_score": crisis_score, "regime": regime}
                )
            return None

        # Mandatory session exit before UTC 21 (at or after 20:00 UTC)
        if ctx.has_position():
            if current_hour >= 20 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "session_close_utc20", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Entry gating: Only evaluate entry at US market opening window (UTC 13 or 14)
        if current_hour not in (13, 14):
            return None

        # Strictly enforce one trade per calendar day & check cooldown
        if self.last_traded_day == current_day or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Measure 12-hour pre-session momentum
        p_now = closes[-1]
        p_prev = closes[-1 - self.lookback]
        ret_12h = (p_now - p_prev) / p_prev

        # Normalize momentum using volatility (ATR)
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        closes_for_atr = ctx.closes(20)
        atr = self._atr(highs, lows, closes_for_atr, period=14)
        if atr is None or atr <= 0:
            return None

        vol_norm_ret = (ret_12h * p_now) / atr  # momentum in multiples of ATR

        # Context filter: avoid buying into extreme fear or selling into extreme greed
        fgi = ctx.features.get("fear_greed_index", 50.0)

        # Thresholds: significant directional drift (> 1.25 ATR move over 12 hours)
        if vol_norm_ret > 1.25 and fgi > 30.0:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 8
            conf = min(0.9, 0.55 + 0.15 * min(vol_norm_ret / 2.5, 1.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "ret_12h_pct": round(ret_12h * 100, 2),
                    "vol_norm_ret": round(vol_norm_ret, 2),
                    "fgi": fgi,
                    "hour": current_hour
                }
            )

        if vol_norm_ret < -1.25 and fgi < 70.0:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 8
            conf = min(0.9, 0.55 + 0.15 * min(abs(vol_norm_ret) / 2.5, 1.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "ret_12h_pct": round(ret_12h * 100, 2),
                    "vol_norm_ret": round(vol_norm_ret, 2),
                    "fgi": fgi,
                    "hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 4)