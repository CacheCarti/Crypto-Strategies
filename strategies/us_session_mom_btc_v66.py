from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBTC(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours intra-session hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.entry_start_utc = 13
        self.entry_end_utc = 15
        self.session_close_utc = 21
        self.min_mom_bps = 50.0  # Clear momentum threshold (0.5%)
        self.last_traded_day = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.date()

        # Session close rule: Force exit before or at UTC 21 if holding a position
        if ctx.has_position():
            if current_hour >= self.session_close_utc:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "session_close_utc_cutoff",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Check entry window: only enter near US session open (UTC 13 to 15)
        if not (self.entry_start_utc <= current_hour <= self.entry_end_utc):
            return None

        # One trade per day constraint
        if self.last_traded_day == current_day:
            return None

        # Avoid entries in crisis meltdown regime
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Momentum calculation over last 12 bars (Asian/European session into US open)
        entry_price = ctx.bar.close
        pre_session_close = closes[-self.lookback - 1]
        mom_bps = ((entry_price - pre_session_close) / pre_session_close) * 10000.0

        # Dynamic volatility check: ensure sufficient movement relative to ATR
        atr = self._atr(highs, lows, closes, period=12)
        if atr is None or atr == 0:
            return None
        atr_bps = (atr / entry_price) * 10000.0

        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Bullish continuation: strong positive momentum aligned with non-extreme-fear sentiment
        if mom_bps > self.min_mom_bps and fear_greed >= 25:
            self.last_traded_day = current_day
            confidence = min(0.85, 0.55 + (abs(mom_bps) / 2000.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_bullish_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "fear_greed": fear_greed,
                    "hour": current_hour,
                }
            )

        # Bearish continuation: strong negative momentum aligned with non-extreme-greed sentiment
        elif mom_bps < -self.min_mom_bps and fear_greed <= 75:
            self.last_traded_day = current_day
            confidence = min(0.85, 0.55 + (abs(mom_bps) / 2000.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_bearish_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "fear_greed": fear_greed,
                    "hour": current_hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass