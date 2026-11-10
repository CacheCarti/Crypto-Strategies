from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcUsSessionMomentum(Strategy):
    METADATA = {
        "name": "BtcUsSessionMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.last_trade_day = -1
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
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Session close rule: flatten all positions by 21 UTC
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Only evaluate entries between 13:00 and 15:00 UTC (US market open window)
        if not (13 <= current_hour <= 15):
            return None

        # One trade per day restriction & cooldown check
        if current_day == self.last_trade_day or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Filter out extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Pre-session momentum (last 12 hours)
        p_now = closes[-1]
        p_prev = closes[-1 - self.lookback]
        mom_pct = (p_now - p_prev) / p_prev

        highs = ctx.highs(50)
        lows = ctx.lows(50)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None

        # Normalize 12-bar move by ATR for conviction (require at least 1.5 ATR displacement)
        atr_ratio = abs(p_now - p_prev) / atr
        if atr_ratio < 1.5:
            return None

        # Trend alignment via EMA50
        ema50 = self._ema(closes, 50)
        if ema50 is None:
            return None

        # Volatility-adjusted threshold (~0.75% minimum move over 12 hours)
        min_move_pct = 0.0075

        # Long continuation setup
        if mom_pct > min_move_pct and p_now > ema50:
            self.last_trade_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 12
            conf = min(0.9, 0.6 + (atr_ratio - 1.5) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_pct": round(mom_pct * 100, 2),
                    "atr_ratio": round(atr_ratio, 2),
                    "ema50": round(ema50, 2),
                    "price": p_now
                }
            )

        # Short continuation setup
        if mom_pct < -min_move_pct and p_now < ema50:
            self.last_trade_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 12
            conf = min(0.9, 0.6 + (atr_ratio - 1.5) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_pct": round(mom_pct * 100, 2),
                    "atr_ratio": round(atr_ratio, 2),
                    "ema50": round(ema50, 2),
                    "price": p_now
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Extend cooldown after a loss to avoid revenge-trading
        ret = position.get("return_bps", 0.0)
        if ret < 0:
            self.cooldown_until_bar = ctx.bar_index + 6