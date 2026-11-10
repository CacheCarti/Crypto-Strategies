from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "UsOpenFollowEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours (15:00 UTC to 21:00 UTC)
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_price_cache = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        opens = ctx.opens(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour
        in_pos = ctx.has_position()

        # Enforce hard session exit at or after UTC 21:00
        if in_pos and current_hour >= 21:
            return ctx.signal("flat", confidence=0.8, metadata={
                "reason": "session_close_utc21",
                "hour": current_hour,
                "price": ctx.bar.close
            })

        # Do not enter new trades if already holding or already traded today
        if in_pos or self.last_traded_day == current_day:
            return None

        # Evaluate at UTC 15:00 (covers opening hours UTC 13:00 to 15:00)
        if current_hour != 15:
            return None

        # Filter out meltdown / extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate opening move over the 2-hour window (from open of 13:00 to close of 14:00/open of 15:00)
        # closes[-1] is 14:00 bar close; opens[-2] is 13:00 bar open
        open_start = opens[-2]
        open_end = closes[-1]
        move = open_end - open_start

        # Baseline volatility using 24-period ATR
        atr = self._atr(highs, lows, closes, period=24)
        if atr is None or atr <= 0:
            return None

        move_ratio = abs(move) / atr

        # Require significant opening thrust exceeding recent range (0.8x ATR),
        # but avoid overextension (cap at 2.4x ATR)
        if not (0.8 <= move_ratio <= 2.4):
            return None

        # Trend filter: fast EMA vs slow EMA to align with broader context
        k_fast = 2.0 / (12 + 1)
        ema12 = sum(closes[-24:-12]) / 12
        for v in closes[-12:]:
            ema12 = v * k_fast + ema12 * (1 - k_fast)

        k_slow = 2.0 / (26 + 1)
        ema26 = sum(closes[:26]) / 26
        for v in closes[26:]:
            ema26 = v * k_slow + ema26 * (1 - k_slow)

        self.last_traded_day = current_day
        conf = min(0.9, max(0.5, 0.4 + (move_ratio * 0.2)))

        if move > 0 and closes[-1] >= ema12:
            return ctx.signal("long", confidence=conf, metadata={
                "reason": "us_open_bullish_thrust",
                "move_ratio": round(move_ratio, 2),
                "atr": round(atr, 2),
                "ema12": round(ema12, 2),
                "ema26": round(ema26, 2),
                "price": closes[-1]
            })
        elif move < 0 and closes[-1] <= ema12:
            return ctx.signal("short", confidence=conf, metadata={
                "reason": "us_open_bearish_thrust",
                "move_ratio": round(move_ratio, 2),
                "atr": round(atr, 2),
                "ema12": round(ema12, 2),
                "ema26": round(ema26, 2),
                "price": closes[-1]
            })

        return None