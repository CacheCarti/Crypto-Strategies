from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max ~6 hours intraday hold
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = None
        self.lookback_bars = 12
        self.entry_start_hour = 13
        self.entry_end_hour = 15
        self.exit_hour = 21
        self.min_return_bps = 70.0       # Threshold to confirm directional conviction
        self.max_return_bps = 350.0      # Cap to avoid chasing overextended / exhausted moves

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
        closes = ctx.closes(self.lookback_bars + 15)
        highs = ctx.highs(self.lookback_bars + 15)
        lows = ctx.lows(self.lookback_bars + 15)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_day = (current_time.year, current_time.month, current_time.day)
        hour = current_time.hour
        has_pos = ctx.has_position()

        # Strict intraday exit rule: close out before or at UTC 21
        if has_pos and hour >= self.exit_hour:
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "session_close_pre_21utc",
                    "hour": hour,
                    "close": ctx.bar.close
                }
            )

        # Do not enter if we already hold a position or already traded today
        if has_pos or self.last_traded_day == current_day:
            return None

        # Only evaluate entries right at session open (UTC 13-15)
        if not (self.entry_start_hour <= hour <= self.entry_end_hour):
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.6 or regime == "MELTDOWN":
            return None

        # 12-bar momentum calculation
        p_now = closes[-1]
        p_past = closes[-(self.lookback_bars + 1)]
        ret_bps = ((p_now - p_past) / p_past) * 10000.0
        abs_ret = abs(ret_bps)

        # Require a clear trend, but avoid parabolic exhausted moves
        if abs_ret < self.min_return_bps or abs_ret > self.max_return_bps:
            return None

        # ATR-based volatility check to ensure sufficient market movement
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or (atr / p_now) < 0.0025:  # Skip dead ranges
            return None

        # Scale confidence by momentum strength
        confidence = min(0.85, max(0.55, 0.55 + (abs_ret - self.min_return_bps) / 400.0))

        if ret_bps > 0:
            self.last_traded_day = current_day
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_long_continuation",
                    "ret_bps_12": round(ret_bps, 2),
                    "hour": hour,
                    "atr": round(atr, 2),
                    "price": p_now
                }
            )
        elif ret_bps < 0:
            self.last_traded_day = current_day
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_short_continuation",
                    "ret_bps_12": round(ret_bps, 2),
                    "hour": hour,
                    "atr": round(atr, 2),
                    "price": p_now
                }
            )

        return None