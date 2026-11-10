from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "Streak Exhaustion Reversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 4

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
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        # Manage existing open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            curr_bar_green = ctx.bar.close > ctx.bar.open
            curr_bar_red = ctx.bar.close < ctx.bar.open

            # Exit on snapback bar or short time-stop
            if pos_dir == "long" and (curr_bar_green or bars_held >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_streak_snap_or_timestop", "bars_held": bars_held, "green": curr_bar_green}
                )
            elif pos_dir == "short" and (curr_bar_red or bars_held >= self.max_hold_bars):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_streak_snap_or_timestop", "bars_held": bars_held, "red": curr_bar_red}
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter (avoid crisis/meltdown)
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate consecutive directional streak
        up_streak = 0
        down_streak = 0
        idx = len(closes) - 1

        while idx >= 0:
            c = closes[idx]
            o = opens[idx]
            if c > o:
                if down_streak > 0:
                    break
                up_streak += 1
            elif c < o:
                if up_streak > 0:
                    break
                down_streak += 1
            else:
                break
            idx -= 1

        # Check volatility and momentum boundaries to avoid runaway trends
        atr = self._atr(highs, lows, closes, 14)
        rsi = self._rsi(closes, 14)

        if atr is None or rsi is None or atr <= 0.0:
            return None

        # Cumulative displacement across streak
        if down_streak >= self.min_streak:
            streak_displacement = opens[-down_streak] - closes[-1]
            # Ensure significant expansion relative to ATR
            if streak_displacement > (atr * 1.5) and rsi < 36.0:
                confidence = min(0.60 + (down_streak - self.min_streak) * 0.10, 0.95)
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=220.0,
                    take_profit_bps=340.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "exhaustion_down_streak_reversal",
                        "streak": down_streak,
                        "rsi": round(rsi, 2),
                        "displacement_atr": round(streak_displacement / atr, 2),
                    }
                )

        if up_streak >= self.min_streak:
            streak_displacement = closes[-1] - opens[-up_streak]
            if streak_displacement > (atr * 1.5) and rsi > 64.0:
                confidence = min(0.60 + (up_streak - self.min_streak) * 0.10, 0.95)
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=220.0,
                    take_profit_bps=340.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "exhaustion_up_streak_reversal",
                        "streak": up_streak,
                        "rsi": round(rsi, 2),
                        "displacement_atr": round(streak_displacement / atr, 2),
                    }
                )

        return None