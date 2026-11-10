from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 3

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        volumes = ctx.volumes(40)

        if len(closes) < 40:
            return None

        ts = ctx.bar.timestamp
        current_hour = ts.hour
        current_date = ts.date()

        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_cutoff", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        if self.last_traded_date == current_date:
            return None

        # Execute only around the US session opening window
        if current_hour not in (13, 14):
            return None

        # Volatility & Momentum calculation over pre-US 12 bars
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        mom_12 = (closes[-1] - closes[-13]) / closes[-13]
        mom_bps = mom_12 * 10000.0

        # Require significant displacement relative to volatility
        avg_vol = sum(volumes[-12:]) / 12.0
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

        # Regime safety filter from market context
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Selective momentum threshold: at least 70 bps move, supported by active volume
        min_move_bps = 70.0
        if abs(mom_bps) < min_move_bps or vol_ratio < 0.9:
            return None

        # Long continuation setup
        if mom_bps >= min_move_bps and closes[-1] > max(closes[-6:-1]):
            self.last_traded_date = current_date
            conf = min(0.9, 0.55 + (abs(mom_bps) / 400.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_long_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "price": closes[-1]
                }
            )

        # Short continuation setup
        if mom_bps <= -min_move_bps and closes[-1] < min(closes[-6:-1]):
            self.last_traded_date = current_date
            conf = min(0.9, 0.55 + (abs(mom_bps) / 400.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_short_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "price": closes[-1]
                }
            )

        return None