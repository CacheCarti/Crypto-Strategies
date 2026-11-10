from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # Max ~6 hours intraday hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.lookback = 12
        self.min_return_bps = 50.0  # Clear direction boundary: at least 0.5% move in 12h
        self.cooldown_bars = 4
        self.bars_since_exit = 10

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_dt = ctx.bar.timestamp
        current_hour = current_dt.hour
        current_date = current_dt.date()

        # Hard session close: mandatory exit at or after UTC 20:00 (before 21:00 boundary)
        if ctx.has_position():
            if current_hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_pre_21_utc",
                        "hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Entry logic: strictly fired on the US session open window at UTC 13:00 or 14:00
        # Exactly one trade allowed per calendar day
        if current_hour not in (13, 14):
            return None

        if self.last_traded_date == current_date:
            return None

        if self.bars_since_exit < self.cooldown_bars:
            return None

        # Avoid extreme crisis or meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate exact 12-hour pre-session momentum
        p_now = closes[-1]
        p_past = closes[-(self.lookback + 1)]
        ret_bps = ((p_now - p_past) / p_past) * 10000.0

        # Breakout confirmation: price must clear the recent 12-bar midpoint with conviction
        half_lookback = self.lookback // 2
        recent_high = max(highs[-half_lookback:])
        recent_low = min(lows[-half_lookback:])

        atr = self._atr(highs, lows, closes, period=12)
        if atr is None or atr <= 0:
            return None

        atr_pct = (atr / p_now) * 10000.0

        # Require meaningful pre-session displacement relative to volatility
        if abs(ret_bps) < max(self.min_return_bps, atr_pct * 0.75):
            return None

        fgi = ctx.features.get("fear_greed_index", 50)

        # Long continuation: positive pre-market drift breaking recent highs
        if ret_bps > 0 and ctx.bar.close >= recent_high and fgi >= 25:
            self.last_traded_date = current_date
            confidence = min(0.9, 0.55 + (abs(ret_bps) / 2000.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_continuation_long",
                    "ret_bps": round(ret_bps, 2),
                    "fgi": fgi,
                    "hour": current_hour,
                    "close": p_now,
                    "recent_high": recent_high
                }
            )

        # Short continuation: negative pre-market drift breaking recent lows
        elif ret_bps < 0 and ctx.bar.close <= recent_low and fgi <= 75:
            self.last_traded_date = current_date
            confidence = min(0.9, 0.55 + (abs(ret_bps) / 2000.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_continuation_short",
                    "ret_bps": round(ret_bps, 2),
                    "fgi": fgi,
                    "hour": current_hour,
                    "close": p_now,
                    "recent_low": recent_low
                }
            )

        return None