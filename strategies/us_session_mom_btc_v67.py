from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours max intra-session
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.atr_period = 14
        self.last_traded_day = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.atr_period + 5)
        highs = ctx.highs(self.lookback + self.atr_period + 5)
        lows = ctx.lows(self.lookback + self.atr_period + 5)

        if len(closes) < self.lookback + self.atr_period:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_key = current_time.toordinal()

        # Hard timed exit at or after UTC 21:00
        if ctx.has_position() and hour >= 21:
            return ctx.signal(
                "flat",
                confidence=1.0,
                metadata={
                    "reason": "us_session_timed_close",
                    "hour": hour,
                    "close": ctx.bar.close
                }
            )

        # Do not enter past UTC 15 (entry window is early session: UTC 13 - 15)
        # Avoid meltdown or extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.6 or market_regime == "MELTDOWN":
            return None

        # One position per day + Cooldown check
        if ctx.bar_index < self.cooldown_until_bar or self.last_traded_day == day_key:
            return None

        # Look for entry strictly at US session kickoff (UTC 13 or 14)
        if not (13 <= hour <= 14):
            return None

        if ctx.has_position():
            return None

        # Momentum calculation over prior 12 bars (Asian/European session trend)
        prior_close = closes[-self.lookback]
        curr_close = closes[-1]
        raw_momentum_pct = (curr_close - prior_close) / prior_close

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Volatility-based threshold: require movement to exceed 0.8 * ATR
        atr_move_threshold = (atr * 0.8) / curr_close
        fg_index = ctx.features.get("fear_greed_index", 50)

        # Dynamic volatility stop calculation: 1.5 * ATR in basis points
        vol_stop_bps = max(100.0, min(300.0, (1.5 * atr / curr_close) * 10000))
        vol_tp_bps = vol_stop_bps * 1.8

        # Long Continuation: strong pre-US rally, not fighting extreme market panic
        if raw_momentum_pct > atr_move_threshold and fg_index > 25:
            self.last_traded_day = day_key
            self.cooldown_until_bar = ctx.bar_index + 6
            confidence = min(0.9, 0.5 + abs(raw_momentum_pct) * 10)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=vol_stop_bps,
                take_profit_bps=vol_tp_bps,
                metadata={
                    "reason": "us_session_open_long_continuation",
                    "hour": hour,
                    "momentum_pct": round(raw_momentum_pct * 100, 3),
                    "atr_bps": round((atr / curr_close) * 10000, 1),
                    "stop_bps": round(vol_stop_bps, 1),
                    "fear_greed": fg_index
                }
            )

        # Short Continuation: strong pre-US breakdown, not fighting extreme market euphoria
        elif raw_momentum_pct < -atr_move_threshold and fg_index < 75:
            self.last_traded_day = day_key
            self.cooldown_until_bar = ctx.bar_index + 6
            confidence = min(0.9, 0.5 + abs(raw_momentum_pct) * 10)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=vol_stop_bps,
                take_profit_bps=vol_tp_bps,
                metadata={
                    "reason": "us_session_open_short_continuation",
                    "hour": hour,
                    "momentum_pct": round(raw_momentum_pct * 100, 3),
                    "atr_bps": round((atr / curr_close) * 10000, 1),
                    "stop_bps": round(vol_stop_bps, 1),
                    "fear_greed": fg_index
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce minimum cooldown of 4 bars after exit
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 4)