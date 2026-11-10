from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum Continuation BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max 6 hours within session
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
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
        # Enforce cooldown after trade closes
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        
        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        hour = current_time.hour
        
        # Hard exit before session end (at or after UTC 21)
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "session_close_cutoff", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Check entry window: US session open/early hours (UTC 13 to 15)
        if hour < 13 or hour > 15:
            return None

        # One trade per day limit & post-exit cooldown
        if self.last_traded_date == current_date:
            return None
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Regime filter: Skip extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Measure 12-bar pre-session / session-open momentum
        # Bar -1 is current, Bar -13 is 12 bars prior
        mom_return = (closes[-1] - closes[-13]) / closes[-13]
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Normalize momentum relative to volatility
        norm_mom = (closes[-1] - closes[-13]) / atr

        # Selective conviction threshold: price moved at least ~1.75 ATR over 12 bars
        # and has minimum percentage hurdle of 0.8% (80 bps)
        min_ret_hurdle = 0.008
        min_atr_hurdle = 1.75

        # Check macro sentiment alignment via Fear & Greed feature
        fg_index = ctx.features.get("fear_greed_index", 50)

        # Bullish Continuation: Strong positive 12-bar push & not in extreme fear
        if norm_mom >= min_atr_hurdle and mom_return >= min_ret_hurdle and fg_index >= 25:
            self.last_traded_date = current_date
            confidence = min(1.0, 0.5 + (norm_mom - min_atr_hurdle) * 0.15)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bull_momentum_continuation",
                    "norm_mom": round(norm_mom, 2),
                    "mom_return_bps": round(mom_return * 10000, 1),
                    "atr": round(atr, 2),
                    "fg_index": fg_index,
                    "hour": hour
                }
            )

        # Bearish Continuation: Strong negative 12-bar push & not in extreme greed
        if norm_mom <= -min_atr_hurdle and mom_return <= -min_ret_hurdle and fg_index <= 75:
            self.last_traded_date = current_date
            confidence = min(1.0, 0.5 + (abs(norm_mom) - min_atr_hurdle) * 0.15)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bear_momentum_continuation",
                    "norm_mom": round(norm_mom, 2),
                    "mom_return_bps": round(mom_return * 10000, 1),
                    "atr": round(atr, 2),
                    "fg_index": fg_index,
                    "hour": hour
                }
            )

        return None