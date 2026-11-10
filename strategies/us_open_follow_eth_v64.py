from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar = -1
        self.cooldown_bars = 4

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after position close
        if ctx.bar_index - self.entry_bar < self.cooldown_bars:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_of_year = current_time.timetuple().tm_yday

        # Mandatory session exit at or after UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": hour, "close": ctx.bar.close}
                )
            return None

        # Entry window: strictly at UTC 15:00 (closing of the 13:00-15:00 window, start of afternoon)
        if hour != 15:
            return None

        # Strictly enforce one trade per day
        if self.last_traded_day == day_of_year:
            return None

        # Calculate opening 2-hour move (from close at UTC 13:00 to close at UTC 15:00)
        # closes[-1] is 15:00 bar close, closes[-3] is 13:00 bar close
        two_hour_delta = closes[-1] - closes[-3]
        two_hour_pct = (two_hour_delta / closes[-3]) * 100.0

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0.0:
            return None

        # Opening move must exceed 1.1x recent hourly ATR to demonstrate clear expansion
        min_move_required = 1.1 * atr
        if abs(two_hour_delta) < min_move_required:
            return None

        # Cross-asset confirmation from BTC (feature feed) to avoid idiosyncratic fakeouts
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # RSI filter to prevent buying an overextended climax move
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Long continuation setup
        if two_hour_pct > 0.0 and btc_ret > -0.5 and 45.0 < rsi < 72.0:
            self.last_traded_day = day_of_year
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_long_continuation",
                    "two_hour_pct": round(two_hour_pct, 3),
                    "btc_return_pct": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2),
                }
            )

        # Short continuation setup
        if two_hour_pct < 0.0 and btc_ret < 0.5 and 28.0 < rsi < 55.0:
            self.last_traded_day = day_of_year
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_short_continuation",
                    "two_hour_pct": round(two_hour_pct, 3),
                    "btc_return_pct": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2),
                }
            )

        return None