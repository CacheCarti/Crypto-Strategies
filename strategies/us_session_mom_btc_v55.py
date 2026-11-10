from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours maximum hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.exit_bar: int = -999
        self.cooldown_bars: int = 4
        self.momentum_period: int = 12
        self.min_return_bps: float = 65.0  # require distinct direction (>0.65% over 12h)

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Hard session close: close position at or after UTC 21
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_end_flatten",
                        "hour": current_hour,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.75:
            return None

        # Entry window: strictly at US session open (UTC 13 or 14) and max one trade per day
        if current_hour not in (13, 14):
            return None

        if self.last_traded_day == current_day:
            return None

        # Momentum calculation over prior 12 hours
        past_price = closes[-self.momentum_period]
        curr_price = closes[-1]
        ret_bps = ((curr_price - past_price) / past_price) * 10000.0

        # Trend baseline filter (30-bar SMA)
        sma30 = self._sma(closes, 30)
        if sma30 is None:
            return None

        # Volatility check
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None
        atr_pct = (atr / curr_price) * 10000.0

        # Long continuation condition
        if ret_bps > self.min_return_bps and curr_price > sma30:
            self.last_traded_day = current_day
            confidence = min(0.9, 0.55 + (ret_bps / 500.0) * 0.25)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "ret_12h_bps": round(ret_bps, 2),
                    "price": curr_price,
                    "sma30": round(sma30, 2),
                    "atr_bps": round(atr_pct, 2)
                }
            )

        # Short continuation condition
        if ret_bps < -self.min_return_bps and curr_price < sma30:
            self.last_traded_day = current_day
            confidence = min(0.9, 0.55 + (abs(ret_bps) / 500.0) * 0.25)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "ret_12h_bps": round(ret_bps, 2),
                    "price": curr_price,
                    "sma30": round(sma30, 2),
                    "atr_bps": round(atr_pct, 2)
                }
            )

        return None