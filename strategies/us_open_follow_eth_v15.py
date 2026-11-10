from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold (UTC 15 to 21)
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_date = current_time.date()

        # Hard session close by UTC 21
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                pos_dir = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_utc_21",
                        "hour": current_hour,
                        "position_direction": pos_dir,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Gate to exactly one trade per day, trigger precisely at UTC 15:00
        # UTC 15 bar close reflects price action over 13:00-15:00 window
        if current_hour != 15:
            return None

        if self.last_traded_date == current_date:
            return None

        # Filter out high-stress crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Calculate opening 2-hour move: bar ending at 15 vs close of bar at 13 (2 bars ago)
        c_now = closes[-1]
        c_open_session = closes[-3]  # close 2 bars prior approximates UTC 13 baseline
        session_move = c_now - c_open_session

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Baseline expansion: move must exceed 1.1x ATR, but not exceed 3.2x ATR (prevent exhaustion)
        move_size = abs(session_move)
        move_ratio = move_size / atr

        if move_ratio < 1.1 or move_ratio > 3.2:
            return None

        # Trend filter: check 24-hour SMA to ensure alignment with prevailing drift
        sma_24 = sum(closes[-24:]) / 24.0

        # Long continuation setup
        if session_move > 0 and c_now > sma_24:
            self.last_traded_date = current_date
            confidence = min(0.9, 0.55 + (move_ratio - 1.1) * 0.15)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_ratio": round(move_ratio, 2),
                    "atr": round(atr, 2),
                    "session_move": round(session_move, 2),
                    "sma_24": round(sma_24, 2),
                    "price": c_now,
                },
            )

        # Short continuation setup
        if session_move < 0 and c_now < sma_24:
            self.last_traded_date = current_date
            confidence = min(0.9, 0.55 + (move_ratio - 1.1) * 0.15)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_ratio": round(move_ratio, 2),
                    "atr": round(atr, 2),
                    "session_move": round(session_move, 2),
                    "sma_24": round(sma_24, 2),
                    "price": c_now,
                },
            )

        return None