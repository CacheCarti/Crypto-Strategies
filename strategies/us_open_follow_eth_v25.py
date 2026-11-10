from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours (15:00 to 21:00 UTC)
        "warmup_bars": 48,
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
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        if len(closes) < 48:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        current_hour = current_time.hour

        # Hard exit at or after 21:00 UTC
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Entry window: exactly evaluated at 15:00 UTC (after UTC 13:00 and 14:00 bars close)
        if current_hour != 15:
            return None

        # One trade per day restriction & cooldown
        if self.last_traded_date == current_date:
            return None
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Market regime filter: avoid extreme crisis environments
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.6 or regime == "MELTDOWN":
            return None

        highs = ctx.highs(48)
        lows = ctx.lows(48)
        opens = ctx.opens(48)

        atr = self._atr(highs, lows, closes, period=24)
        if atr is None or atr <= 0.0:
            return None

        # Opening 2-hour window: bar at index -2 (13:00) and bar at index -1 (14:00)
        open_13 = opens[-2]
        close_14 = closes[-1]
        session_high = max(highs[-2], highs[-1])
        session_low = min(lows[-2], lows[-1])
        open_move = close_14 - open_13
        abs_move = abs(open_move)

        # Baseline: 24h rolling median hourly bar range
        bar_ranges = [highs[i] - lows[i] for i in range(-24, 0)]
        sorted_ranges = sorted(bar_ranges)
        median_range = sorted_ranges[len(sorted_ranges) // 2]

        # Condition 1: Significant directional move exceeding 1.35x recent median range
        if abs_move < 1.35 * median_range or abs_move < 0.6 * atr:
            return None

        # Condition 2: Session candle conviction (closed near the extreme of the 2-hour session)
        session_span = session_high - session_low
        if session_span <= 0:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Long continuation setup
        if open_move > 0:
            # Bullish conviction: closed in top 25% of the opening 2-hour range
            close_pct = (close_14 - session_low) / session_span
            if close_pct < 0.75:
                return None
            # Not overbought (prevent top-ticking)
            if rsi > 70.0:
                return None

            self.last_traded_date = current_date
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "open_move": round(open_move, 2),
                    "median_range": round(median_range, 2),
                    "close_pct": round(close_pct, 2),
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2)
                }
            )

        # Short continuation setup
        elif open_move < 0:
            # Bearish conviction: closed in bottom 25% of the opening 2-hour range
            close_pct = (close_14 - session_low) / session_span
            if close_pct > 0.25:
                return None
            # Not oversold (prevent bottom-selling)
            if rsi < 30.0:
                return None

            self.last_traded_date = current_date
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "open_move": round(open_move, 2),
                    "median_range": round(median_range, 2),
                    "close_pct": round(close_pct, 2),
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2)
                }
            )

        return None