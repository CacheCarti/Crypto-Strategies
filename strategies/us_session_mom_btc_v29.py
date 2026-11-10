from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until_bar = 0

    def _stddev(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        sub = values[-period:]
        mean = sum(sub) / period
        variance = sum((x - mean) ** 2 for x in sub) / period
        return math.sqrt(variance)

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_date = current_time.date()

        # Regime filter: Skip trading during volatile meltdown/crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "regime": regime, "price": ctx.bar.close}
                )
            return None

        # Mandatory session exit before UTC 21:00
        if ctx.has_position() and current_hour >= 21:
            return ctx.signal(
                "flat",
                confidence=0.7,
                metadata={
                    "reason": "session_close_utc21",
                    "hour": current_hour,
                    "price": ctx.bar.close
                }
            )

        # Allow new entry only at session open window (UTC 13 or 14), once per day, outside cooldown
        if ctx.has_position() or ctx.bar_index < self.cooldown_until_bar:
            return None

        if current_hour not in (13, 14):
            return None

        if self.last_traded_date == current_date:
            return None

        # Measure pre-session 12-bar normalized momentum
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None

        lookback = 12
        momentum = closes[-1] - closes[-lookback]
        # State-based momentum normalized by current ATR volatility
        norm_mom = momentum / atr

        # Volatility normalization: ensure sufficient dispersion without excessive chop
        std20 = self._stddev(closes, 20)
        if std20 is None or std20 == 0:
            return None

        # Broad macro sentiment filter to avoid fighting extreme fear / greed divergence
        fg_index = ctx.features.get("fear_greed_index", 50)

        # Continuation Long: Prior 12h clearly up (norm_mom >= 1.2 ATR) and not in extreme greed trap
        if norm_mom >= 1.2 and fg_index <= 80:
            self.last_traded_date = current_date
            confidence = min(0.85, 0.55 + 0.1 * min(norm_mom, 3.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_bull_continuation",
                    "norm_mom": round(norm_mom, 3),
                    "atr": round(atr, 2),
                    "fear_greed": fg_index,
                    "hour": current_hour,
                    "price": ctx.bar.close
                }
            )

        # Continuation Short: Prior 12h clearly down (norm_mom <= -1.2 ATR) and not in extreme fear bounce
        if norm_mom <= -1.2 and fg_index >= 20:
            self.last_traded_date = current_date
            confidence = min(0.85, 0.55 + 0.1 * min(abs(norm_mom), 3.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_bear_continuation",
                    "norm_mom": round(norm_mom, 3),
                    "atr": round(atr, 2),
                    "fear_greed": fg_index,
                    "hour": current_hour,
                    "price": ctx.bar.close
                }
            )

        return None