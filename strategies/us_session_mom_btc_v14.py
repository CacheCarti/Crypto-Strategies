from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # Max ~6 hours during US session
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown of 3 bars after position closes
        self.cooldown_until_bar = ctx.bar_index + 3

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Check existing position for session exit rule
        if ctx.has_position():
            # Mandatory flat exit by session end (UTC 21) or beyond
            if current_hour >= self.session_end_hour or current_hour < self.session_start_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_time_exit", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Guard: cooldown period active
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Guard: only 1 entry attempt per day, fired specifically at session onset (UTC 13 or 14)
        if current_day == self.last_trade_day:
            return None

        if current_hour not in (self.session_start_hour, self.session_start_hour + 1):
            return None

        # 1. Measure pre-session 12-bar trend direction
        ret_12 = (closes[-1] - closes[-13]) / closes[-13]

        # 2. Material difference from failed prior family: Volatility & Funding Rate state filter
        # Compute normalized ATR to verify volatility expansion
        atr_val = self._atr(highs, lows, closes, period=14)
        if atr_val is None:
            return None
        atr_pct = atr_val / closes[-1]

        # Require minimum active volatility (> 0.35%) to avoid whipsaw chop
        if atr_pct < 0.0035:
            return None

        # Cross-market state: Funding rate filter to prevent entering crowded retail extremes
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Crisis filter from market context
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Momentum threshold: 0.8% over past 12 hours (strong pre-session setup)
        threshold = 0.008

        if ret_12 > threshold and funding < 0.0004:
            self.last_trade_day = current_day
            conf = min(0.9, 0.5 + (ret_12 / 0.05) * 0.4)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_long_momentum_continuation",
                    "ret_12": round(ret_12, 4),
                    "atr_pct": round(atr_pct, 4),
                    "funding": funding,
                    "hour": current_hour
                }
            )

        elif ret_12 < -threshold and funding > -0.0004:
            self.last_trade_day = current_day
            conf = min(0.9, 0.5 + (abs(ret_12) / 0.05) * 0.4)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_short_momentum_continuation",
                    "ret_12": round(ret_12, 4),
                    "atr_pct": round(atr_pct, 4),
                    "funding": funding,
                    "hour": current_hour
                }
            )

        return None