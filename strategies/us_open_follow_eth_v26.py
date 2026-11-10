from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max intraday hold ~6 hours (UTC 15 to 21)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar_idx = -1
        self.cooldown_bars = 4

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar_idx = ctx.bar_index

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return statistics.mean(trs[-period:])

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        
        if len(closes) < 40:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Hard intraday session exit: close by UTC 21 regardless
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "close": ctx.bar.close}
                )
            return None

        # Enforce cooldown after any position exit
        if ctx.bar_index - self.entry_bar_idx < self.cooldown_bars:
            return None

        # Entry window: Evaluate specifically at UTC 15:00 (after UTC 13:00 and 14:00 bars close)
        if current_hour != 15:
            return None

        # One trade per calendar day restriction
        if current_day == self.last_traded_day:
            return None

        # Opening move: performance from start of UTC 13:00 bar (open of closes[-2]) to close of UTC 14:00 bar (closes[-1])
        # ctx.opens(3)[-2] is open at UTC 13:00; ctx.closes(3)[-1] is close at UTC 14:00
        opens = ctx.opens(5)
        us_open_price = opens[-2]
        us_curr_price = closes[-1]
        move_bps = ((us_curr_price - us_open_price) / us_open_price) * 10000.0

        # Baseline volatility using 24-period median hourly range
        hourly_ranges = [h - l for h, l in zip(highs[-24:], lows[-24:])]
        median_range = statistics.median(hourly_ranges)
        median_range_bps = (median_range / us_open_price) * 10000.0

        # Require opening move magnitude to exceed recent median hourly range
        if abs(move_bps) < median_range_bps:
            return None

        # Guard against extreme overextension where momentum exhausts
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_bps = (atr / ctx.bar.close) * 10000.0
        if abs(move_bps) > 3.0 * atr_bps:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Market trend filter
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long Continuation: Solid positive opening move, not overbought (RSI < 68), no bear trend conflict
        if move_bps > 0 and 45.0 < rsi < 68.0 and trend_regime != "bear":
            self.last_traded_day = current_day
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_bps": round(move_bps, 2),
                    "median_range_bps": round(median_range_bps, 2),
                    "rsi": round(rsi, 2),
                    "trend_regime": trend_regime,
                }
            )

        # Short Continuation: Solid negative opening move, not oversold (RSI > 32), no bull trend conflict
        if move_bps < 0 and 32.0 < rsi < 55.0 and trend_regime != "bull":
            self.last_traded_day = current_day
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_bps": round(move_bps, 2),
                    "median_range_bps": round(median_range_bps, 2),
                    "rsi": round(rsi, 2),
                    "trend_regime": trend_regime,
                }
            )

        return None