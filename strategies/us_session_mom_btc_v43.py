from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours maximum hold (within session)
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until_bar = 0
        self.lookback_momentum = 12
        self.atr_period = 14
        self.ema_filter_period = 50

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown after position exit
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_momentum + self.ema_filter_period + 5)
        highs = ctx.highs(self.atr_period + 5)
        lows = ctx.lows(self.atr_period + 5)

        if len(closes) < max(self.lookback_momentum + 1, self.ema_filter_period):
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_date = current_time.date()

        # Session Boundary Enforcement: Must exit before or at 21:00 UTC
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_close_timeout",
                        "hour": current_hour,
                        "close_price": ctx.bar.close
                    }
                )
            return None

        # Entry Gate: One trade per calendar day, strictly at US session onset window (13:00 - 15:00 UTC)
        if current_hour not in (13, 14, 15):
            return None

        if self.last_traded_date == current_date:
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Filter out extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Measure 12-bar pre-session momentum (Asian/European drift)
        p_now = closes[-1]
        p_prev = closes[-1 - self.lookback_momentum]
        mom_return_bps = ((p_now - p_prev) / p_prev) * 10000.0

        # Dynamic volatility threshold via normalized ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or p_now == 0:
            return None
        atr_bps = (atr / p_now) * 10000.0
        min_momentum_bps = max(60.0, atr_bps * 0.75)

        ema50 = self._ema(closes, self.ema_filter_period)
        if ema50 is None:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50)

        # Long continuation setup:
        # Pre-session momentum is decisively up AND price aligns with medium trend (above EMA50)
        if mom_return_bps > min_momentum_bps and p_now > ema50 and fg_index >= 25:
            self.last_traded_date = current_date
            confidence = min(0.85, 0.55 + (mom_return_bps / (min_momentum_bps * 4.0)))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_bull_continuation",
                    "mom_12h_bps": round(mom_return_bps, 2),
                    "min_required_bps": round(min_momentum_bps, 2),
                    "ema50": round(ema50, 2),
                    "price": p_now,
                    "fear_greed": fg_index
                }
            )

        # Short continuation setup:
        # Pre-session momentum is decisively down AND price aligns with medium trend (below EMA50)
        if mom_return_bps < -min_momentum_bps and p_now < ema50 and fg_index <= 75:
            self.last_traded_date = current_date
            confidence = min(0.85, 0.55 + (abs(mom_return_bps) / (min_momentum_bps * 4.0)))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_bear_continuation",
                    "mom_12h_bps": round(mom_return_bps, 2),
                    "min_required_bps": round(min_momentum_bps, 2),
                    "ema50": round(ema50, 2),
                    "price": p_now,
                    "fear_greed": fg_index
                }
            )

        return None