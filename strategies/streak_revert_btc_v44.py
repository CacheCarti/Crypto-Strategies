from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalVolatility(Strategy):
    METADATA = {
        "name": "StreakReversalVolatility",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 4

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 20:
            return None

        # Position Management: Exit on streak-snap (opposite bar) or timed horizon
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            is_green = closes[-1] > opens[-1]
            is_red = closes[-1] < opens[-1]
            bars_held = ctx.bar_index - self.entry_bar

            # Snap exit: if long and bar is green (bounce achieved), or short and bar is red
            if pos_dir == "long" and (is_green or bars_held >= self.max_hold_bars):
                reason = "bounce_realized" if is_green else "time_stop_exhaustion"
                return ctx.signal("flat", confidence=0.5, metadata={"reason": reason, "bars_held": bars_held, "close": closes[-1]})
            
            if pos_dir == "short" and (is_red or bars_held >= self.max_hold_bars):
                reason = "drop_realized" if is_red else "time_stop_exhaustion"
                return ctx.signal("flat", confidence=0.5, metadata={"reason": reason, "bars_held": bars_held, "close": closes[-1]})

            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Count consecutive directional bars ending at previous or current bar
        down_streak = 0
        for c, o in zip(reversed(closes), reversed(opens)):
            if c < o:
                down_streak += 1
            else:
                break

        up_streak = 0
        for c, o in zip(reversed(closes), reversed(opens)):
            if c > o:
                up_streak += 1
            else:
                break

        atr = self._atr(highs, lows, closes, 14)
        rsi = self._rsi(closes, 14)
        if atr is None or rsi is None or closes[-1] <= 0:
            return None

        atr_bps = (atr / closes[-1]) * 10000.0
        # Dynamic volatility stop: structured based on recent ATR to avoid early giveback chop
        sl_bps = max(140.0, min(350.0, atr_bps * 1.5))
        tp_bps = max(160.0, min(400.0, atr_bps * 1.8))

        # Long exhaustion: strong down streak coupled with oversold conditions
        if down_streak >= self.streak_threshold and rsi < 36.0:
            conf = min(0.9, 0.55 + (down_streak - self.streak_threshold) * 0.1)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "streak_reversal_oversold_exhaustion",
                    "streak_count": down_streak,
                    "rsi": round(rsi, 2),
                    "atr_bps": round(atr_bps, 1),
                }
            )

        # Short exhaustion: strong up streak coupled with overbought conditions
        if up_streak >= self.streak_threshold and rsi > 64.0:
            conf = min(0.9, 0.55 + (up_streak - self.streak_threshold) * 0.1)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "streak_reversal_overbought_exhaustion",
                    "streak_count": up_streak,
                    "rsi": round(rsi, 2),
                    "atr_bps": round(atr_bps, 1),
                }
            )

        return None