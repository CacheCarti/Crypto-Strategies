from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours maximum session hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.lookback = 12
        self.min_ret_bps = 75.0
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14) -> Optional[float]:
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
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 20:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Hard exit before session end (UTC 21) or if outside trading window
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "session_close_utc21",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Enforce cooldown and single-trade-per-day restriction
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_day == current_day:
            return None

        # Entry window: initial hours of US session (UTC 13 to 15)
        if current_hour not in (13, 14, 15):
            return None

        # Regime gate to address prior failure (oos_reject): avoid low-volatility chop or severe crises
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Pre-session momentum over the last 12 bars (UTC 01-13 approx)
        past_close = closes[-self.lookback]
        current_close = closes[-1]
        pre_return_bps = ((current_close - past_close) / past_close) * 10000.0

        # Volatility confirmation via ATR to ensure meaningful movement
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        atr_pct = (atr / current_close) * 10000.0
        if atr_pct < 25.0:  # Skip stagnant markets
            return None

        # Sentiment alignment gate
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Bullish continuation: strong positive pre-session momentum not heavily overbought
        if pre_return_bps >= self.min_ret_bps and fg_index >= 25:
            self.last_traded_day = current_day
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "pre_return_bps": round(pre_return_bps, 2),
                    "atr_pct": round(atr_pct, 2),
                    "fg_index": fg_index,
                    "hour": current_hour,
                },
            )

        # Bearish continuation: strong negative pre-session momentum not in extreme greed
        if pre_return_bps <= -self.min_ret_bps and fg_index <= 75:
            self.last_traded_day = current_day
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "pre_return_bps": round(pre_return_bps, 2),
                    "atr_pct": round(atr_pct, 2),
                    "fg_index": fg_index,
                    "hour": current_hour,
                },
            )

        return None