from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcUsSessionMomentum(Strategy):
    METADATA = {
        "name": "BtcUsSessionMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours (within session)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.cooldown_until_bar: int = 0
        self.lookback_bars: int = 12
        self.min_abs_return_pct: float = 0.8  # Minimum threshold for pre-session momentum
        self.session_open_hour: int = 13
        self.session_close_hour: int = 21

    def _atr(self, highs, lows, closes, period=14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour

        # Hard session close: liquidate any remaining position before session ends
        if ctx.has_position():
            if current_hour >= self.session_close_hour or current_hour < self.session_open_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "us_session_end_liquidation",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Cooldown guard after trade exits
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Strictly trade once per day exactly at US open (UTC 13)
        if current_hour != self.session_open_hour:
            return None

        if self.last_traded_day == current_day:
            return None

        # Market regime filter: avoid meltdowns or severe crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Evaluate the prior 12-hour session trend momentum
        pre_closes = ctx.closes(self.lookback_bars + 1)
        if len(pre_closes) < self.lookback_bars + 1:
            return None

        start_p = pre_closes[0]
        end_p = pre_closes[-1]
        momentum_pct = ((end_p - start_p) / start_p) * 100.0

        # Check volatility via ATR to adapt confidence
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        atr_pct = (atr / ctx.bar.close) * 100.0 if atr else 1.0

        # Sentiment alignment filter
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Trigger Long: Strong upward momentum preceding open, not in extreme greed
        if momentum_pct >= self.min_abs_return_pct and fear_greed < 85:
            self.last_traded_day = current_day
            confidence = min(0.9, max(0.55, 0.5 + (momentum_pct / 10.0)))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_continuation_long",
                    "momentum_12h_pct": round(momentum_pct, 3),
                    "fear_greed": fear_greed,
                    "atr_pct": round(atr_pct, 3),
                    "hour": current_hour,
                }
            )

        # Trigger Short: Strong downward momentum preceding open, not in extreme fear
        if momentum_pct <= -self.min_abs_return_pct and fear_greed > 15:
            self.last_traded_day = current_day
            confidence = min(0.9, max(0.55, 0.5 + (abs(momentum_pct) / 10.0)))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_continuation_short",
                    "momentum_12h_pct": round(momentum_pct, 3),
                    "fear_greed": fear_greed,
                    "atr_pct": round(atr_pct, 3),
                    "hour": current_hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown of 4 bars after exit
        self.cooldown_until_bar = ctx.bar_index + 4