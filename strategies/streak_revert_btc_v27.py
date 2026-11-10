from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcExhaustionStreakReversal(Strategy):
    METADATA = {
        "name": "BtcExhaustionStreakReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 14400,  # ~4 hours avg target
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.atr_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        opens = ctx.opens(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 30:
            return None

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            curr_bar_green = ctx.bar.close > ctx.bar.open
            curr_bar_red = ctx.bar.close < ctx.bar.open
            bars_in_trade = ctx.bar_index - self.entry_bar

            # Snap exit: first opposing color candle or max holding period reached
            if pos_dir == "long" and curr_bar_green:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "streak_snap_green_candle", "bars_held": bars_in_trade, "close": ctx.bar.close}
                )
            elif pos_dir == "short" and curr_bar_red:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "streak_snap_red_candle", "bars_held": bars_in_trade, "close": ctx.bar.close}
                )
            elif bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_exhaustion_unresolved", "bars_held": bars_in_trade}
                )
            return None

        # Check cooldown constraint
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Count consecutive identical body directions
        down_streak = 0
        up_streak = 0
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] < opens[i]:
                if up_streak > 0:
                    break
                down_streak += 1
            elif closes[i] > opens[i]:
                if down_streak > 0:
                    break
                up_streak += 1
            else:
                break

        # Event boundary: require the setup threshold to be reached,
        # plus volatility exhaustion: cumulative move must exceed 1.5 ATRs
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Filter out extreme regime crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long exhaustion trigger
        if down_streak >= self.min_streak:
            streak_move = opens[-down_streak] - closes[-1]
            if streak_move >= 1.5 * atr:
                # Scale confidence strictly with streak length (6 -> 0.70, 7 -> 0.80, 8+ -> 0.90)
                conf = min(0.95, 0.70 + (down_streak - self.min_streak) * 0.10)
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=220.0,
                    take_profit_bps=280.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "exhaustion_down_streak_reversal",
                        "streak": down_streak,
                        "move_pts": streak_move,
                        "atr": atr,
                        "ratio": streak_move / atr
                    }
                )

        # Short exhaustion trigger
        if up_streak >= self.min_streak:
            streak_move = closes[-1] - opens[-up_streak]
            if streak_move >= 1.5 * atr:
                conf = min(0.95, 0.70 + (up_streak - self.min_streak) * 0.10)
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=220.0,
                    take_profit_bps=280.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "exhaustion_up_streak_reversal",
                        "streak": up_streak,
                        "move_pts": streak_move,
                        "atr": atr,
                        "ratio": streak_move / atr
                    }
                )

        return None