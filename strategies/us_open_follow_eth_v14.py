from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Session Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.entry_bar_idx: int = -1
        self.cooldown_until_bar: int = -1

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
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        day_id = current_time.year * 1000 + current_time.timetuple().tm_yday

        # Session mandatory exit: flatten by UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or (ctx.bar_index - self.entry_bar_idx >= 7):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "bars_held": ctx.bar_index - self.entry_bar_idx}
                )
            return None

        # Respect cooldown and strictly trade once per calendar day
        if ctx.bar_index < self.cooldown_until_bar or self.last_traded_day == day_id:
            return None

        # Filter crisis or extreme market states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Trigger check at UTC 15:00: eval opening 2h move (13:00 to 15:00)
        if current_hour != 15:
            return None

        highs = ctx.highs(40)
        lows = ctx.lows(40)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0.0:
            return None

        # Two-hour session displacement (from bar -3 close to current bar close)
        open_price_13 = closes[-3]
        current_price = ctx.bar.close
        move = current_price - open_price_13
        abs_move = abs(move)

        # Require move to exceed typical session volatility (1.1 * ATR)
        if abs_move < 1.1 * atr:
            return None

        # Avoid chasing exhausted moves
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Cross-asset alignment: BTC daily return confirmation
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Directional continuation entries
        if move > 0 and rsi < 72.0 and btc_ret >= -0.5:
            self.last_traded_day = day_id
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_ratio": round(abs_move / atr, 2),
                    "rsi": round(rsi, 2),
                    "btc_return_pct": round(btc_ret, 2),
                    "atr": round(atr, 2),
                }
            )

        if move < 0 and rsi > 28.0 and btc_ret <= 0.5:
            self.last_traded_day = day_id
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_ratio": round(abs_move / atr, 2),
                    "rsi": round(rsi, 2),
                    "btc_return_pct": round(btc_ret, 2),
                    "atr": round(atr, 2),
                }
            )

        return None