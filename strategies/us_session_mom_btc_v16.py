from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended session hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.threshold_pct = 0.0075  # 0.75% directional move over 12 hours
        self.last_trade_day = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 20:
            return None

        ts = ctx.bar.timestamp
        hour = ts.hour
        current_day = ts.toordinal()

        # Hard session close: liquidate any open position at UTC 20:00 or later
        if ctx.has_position():
            if hour >= 20:
                pos = ctx.position or {}
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_pre_21_utc",
                        "hour": hour,
                        "entry_price": pos.get("entry_price", 0.0),
                        "close_price": ctx.bar.close
                    }
                )
            return None

        # Only evaluate entries right around the US session opening window (UTC 13 or 14)
        if hour not in (13, 14):
            return None

        # Strictly enforce one trade attempt per day & cooldown
        if current_day == self.last_trade_day or ctx.bar_index < self.cooldown_until_bar:
            return None

        # Avoid entries in crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        mom_return = (current_close - past_close) / past_close

        # Volatility check: require decent movement without excessive blowouts
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / current_close) if current_close > 0 else 0.0
        if atr_pct < 0.003 or atr_pct > 0.045:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Bullish continuation: prior 12h momentum is positive and funding is not excessively overheated
        if mom_return >= self.threshold_pct and funding < 0.0004:
            self.last_trade_day = current_day
            conf = min(0.85, 0.55 + abs(mom_return) * 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_12h": round(mom_return, 4),
                    "funding": round(funding, 6),
                    "atr_pct": round(atr_pct, 4),
                    "hour": hour
                }
            )

        # Bearish continuation: prior 12h momentum is negative and funding is not deeply negative
        if mom_return <= -self.threshold_pct and funding > -0.0004:
            self.last_trade_day = current_day
            conf = min(0.85, 0.55 + abs(mom_return) * 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_12h": round(mom_return, 4),
                    "funding": round(funding, 6),
                    "atr_pct": round(atr_pct, 4),
                    "hour": hour
                }
            )

        return None