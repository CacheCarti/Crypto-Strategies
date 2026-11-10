from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max intraday hold ~6 hours
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.exit_cooldown_bars = 4
        self.bars_since_exit = 100

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
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(40)
        if len(closes) < 30:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.toordinal()

        # Hard intraday session close: exit by UTC 21
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "close": ctx.bar.close}
                )
            return None

        # Entry window: strictly trigger at UTC 15 (after observing 13:00 and 14:00 bars)
        if current_hour != 15:
            return None

        # One trade per calendar day restriction
        if current_day == self.last_trade_day:
            return None

        # Cooldown guard
        if self.bars_since_exit < self.exit_cooldown_bars:
            return None

        # Market regime filter: avoid entering during extreme crisis or meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        highs = ctx.highs(40)
        lows = ctx.lows(40)
        opens = ctx.opens(40)

        # Baseline volatility check
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Evaluate opening move from UTC 13:00 open to UTC 15:00 close (2-hour impulse)
        # Bar -1 is 14:00-15:00, Bar -2 is 13:00-14:00
        open_session_price = opens[-2]
        close_session_price = closes[-1]
        move = close_session_price - open_session_price
        move_bps = (move / open_session_price) * 10000.0

        # Calculate recent median 2-bar range to ensure opening impulse has substance
        two_bar_ranges = [abs(closes[i] - opens[i - 1]) for i in range(2, len(closes))]
        median_range = statistics.median(two_bar_ranges) if two_bar_ranges else atr

        # Require displacement to exceed median 2-bar range and at least 0.75 * ATR
        if abs(move) < median_range or abs(move) < (0.75 * atr):
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Directional entry with non-exhaustion bounds
        # Long continuation: positive session push, not drastically overbought
        if move_bps > 25.0 and rsi < 70.0 and closes[-1] > closes[-2]:
            self.last_trade_day = current_day
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_bps": round(move_bps, 2),
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2),
                    "hour": current_hour
                }
            )

        # Short continuation: negative session push, not drastically oversold
        if move_bps < -25.0 and rsi > 30.0 and closes[-1] < closes[-2]:
            self.last_trade_day = current_day
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_bps": round(move_bps, 2),
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2),
                    "hour": current_hour
                }
            )

        return None