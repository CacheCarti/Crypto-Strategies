from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0
        self.session_start_hour = 13
        self.session_end_hour = 21

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

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit structure: strictly close before session ends (UTC 21)
        if has_pos:
            if current_hour >= self.session_end_hour or current_hour < self.session_start_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_end_exit",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Cooldown guard and one-trade-per-day enforcement
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_traded_day == current_day:
            return None

        # Entry window: Opening hours of the US session (UTC 13 to 15)
        if not (self.session_start_hour <= current_hour <= 15):
            return None

        # Measure pre-session 12-bar momentum
        mom_12 = (closes[-1] - closes[-13]) / closes[-13]
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None

        # Normalized momentum hurdle based on recent volatility
        vol_normalized_move = abs(closes[-1] - closes[-13]) / atr
        if vol_normalized_move < 1.2:
            return None

        # Filter against extreme market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Directional entry
        if mom_12 > 0.006:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 8
            confidence = min(0.9, 0.5 + min(mom_12 * 20.0, 0.4))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_long_momentum",
                    "mom_12": round(mom_12, 5),
                    "vol_normalized_move": round(vol_normalized_move, 2),
                    "hour": current_hour,
                    "close": ctx.bar.close,
                }
            )

        if mom_12 < -0.006:
            self.last_traded_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 8
            confidence = min(0.9, 0.5 + min(abs(mom_12) * 20.0, 0.4))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_short_momentum",
                    "mom_12": round(mom_12, 5),
                    "vol_normalized_move": round(vol_normalized_move, 2),
                    "hour": current_hour,
                    "close": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4