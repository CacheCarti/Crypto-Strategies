from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "UsOpenFollowEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.cooldown_until_bar: int = 0
        self.entry_bar: int = 0

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Exit logic: Close by UTC 21 or after max intraday hold
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if current_hour >= 21 or bars_held >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "bars_held": bars_held}
                )
            return None

        # Guard: One trade per day and mandatory cooldown
        if ctx.bar_index < self.cooldown_until_bar or self.last_traded_day == current_day:
            return None

        # Execution trigger strictly after the 2-hour window (at UTC 15)
        if current_hour != 15:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        rsi = self._rsi(closes, period=14)
        if atr is None or rsi is None or atr <= 0.0:
            return None

        # Two-hour session range: bar index -2 (UTC 13-14) and -1 (UTC 14-15)
        open_price_13 = ctx.opens(3)[-2]
        close_price_15 = closes[-1]
        session_net_move = close_price_15 - open_price_13

        # Thresholds: require move > 0.85 * ATR, capped at 2.4 * ATR (not overextended)
        min_move = 0.85 * atr
        max_move = 2.40 * atr
        abs_move = abs(session_net_move)

        if abs_move < min_move or abs_move > max_move:
            return None

        # Trend and exhaustion filters
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Directional entry
        if session_net_move > 0 and 45.0 <= rsi <= 72.0:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + 7
            confidence = min(0.9, max(0.5, abs_move / (1.5 * atr)))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "session_net_move": session_net_move,
                    "atr": atr,
                    "rsi": rsi,
                    "hour": current_hour
                }
            )

        elif session_net_move < 0 and 28.0 <= rsi <= 55.0:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + 7
            confidence = min(0.9, max(0.5, abs_move / (1.5 * atr)))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "session_net_move": session_net_move,
                    "atr": atr,
                    "rsi": rsi,
                    "hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4