from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "BtcStreakExhaustionReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 6

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

        if len(closes) < 30 or len(opens) < 30:
            return None

        # Check existing position management: timed exit or color flip
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            is_green = closes[-1] > opens[-1]
            is_red = closes[-1] < opens[-1]

            # Structural exit: snap bar occurred or time limit exceeded
            if (pos_dir == "long" and (is_green or bars_held >= self.max_hold_bars)) or \
               (pos_dir == "short" and (is_red or bars_held >= self.max_hold_bars)):
                exit_reason = "snap_bar_reversion" if (is_green if pos_dir == "long" else is_red) else "time_limit_exhaustion"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": exit_reason,
                        "bars_held": bars_held,
                        "close": closes[-1],
                        "open": opens[-1]
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme regime crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Compute streak of consecutive same-colored bars
        down_streak = 0
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] < opens[i]:
                down_streak += 1
            else:
                break

        up_streak = 0
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] > opens[i]:
                up_streak += 1
            else:
                break

        # Compute dynamic ATR-based SL/TP
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or closes[-1] <= 0:
            return None

        atr_bps = (atr / closes[-1]) * 10000.0
        sl_bps = max(150.0, min(350.0, atr_bps * 1.5))
        tp_bps = max(200.0, min(450.0, atr_bps * 1.8))

        rsi = self._rsi(closes, period=14) or 50.0

        # Down streak exhaustion -> Reversal Long
        if down_streak >= self.min_streak and rsi < 45.0:
            conf = min(0.9, 0.55 + (down_streak - self.min_streak) * 0.08)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "exhaustion_down_streak_reversal",
                    "streak_length": down_streak,
                    "rsi": rsi,
                    "atr_bps": atr_bps,
                    "close": closes[-1]
                }
            )

        # Up streak exhaustion -> Reversal Short
        if up_streak >= self.min_streak and rsi > 55.0:
            conf = min(0.9, 0.55 + (up_streak - self.min_streak) * 0.08)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "exhaustion_up_streak_reversal",
                    "streak_length": up_streak,
                    "rsi": rsi,
                    "atr_bps": atr_bps,
                    "close": closes[-1]
                }
            )

        return None