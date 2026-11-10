from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        highs = ctx.highs(48)
        lows = ctx.lows(48)
        opens = ctx.opens(48)

        if len(closes) < 48:
            return None

        ts = ctx.bar.timestamp
        hour = ts.hour
        today_str = ts.strftime("%Y-%m-%d")

        # Session close at UTC 21:00 or later
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_close_utc21",
                    "hour": hour,
                    "close": ctx.bar.close
                })
            return None

        # Check cooldown and one-trade-per-day enforcement
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_date == today_str:
            return None

        # Trigger exactly at UTC 15:00 after observing the 13:00-15:00 2-bar move
        if hour != 15:
            return None

        # Two-hour window: 13:00 open (2 bars ago open) to 15:00 open / close of 14:00 (1 bar ago close)
        open_13 = opens[-2]
        close_14 = closes[-2]
        current_close = closes[-1]
        move = close_14 - open_13
        move_pct = move / open_13

        # Compute median 2-bar range over recent history
        ranges = [abs(closes[i] - opens[i - 1]) for i in range(1, len(closes))]
        median_range = statistics.median(ranges) if len(ranges) >= 10 else 0.0

        if abs(move) <= median_range or median_range == 0.0:
            return None

        atr = self._atr(highs, lows, closes, 14)
        rsi = self._rsi(closes, 14)

        if atr is None or rsi is None:
            return None

        # Regime safety filter: avoid severe market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Opening drive confirmed: move exceeds 0.75 * ATR and not already exhausted
        atr_multiple = abs(move) / atr

        # Directional follow-through setup
        if move > 0 and 0.65 <= atr_multiple <= 2.8 and rsi < 72.0:
            self.last_traded_date = today_str
            self.cooldown_until_bar = ctx.bar_index + 6
            confidence = min(0.85, 0.55 + 0.1 * (atr_multiple - 0.65))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_pct": move_pct,
                    "atr_multiple": atr_multiple,
                    "rsi": rsi,
                    "price": current_close
                }
            )

        if move < 0 and 0.65 <= atr_multiple <= 2.8 and rsi > 28.0:
            self.last_traded_date = today_str
            self.cooldown_until_bar = ctx.bar_index + 6
            confidence = min(0.85, 0.55 + 0.1 * (atr_multiple - 0.65))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_pct": move_pct,
                    "atr_multiple": atr_multiple,
                    "rsi": rsi,
                    "price": current_close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4