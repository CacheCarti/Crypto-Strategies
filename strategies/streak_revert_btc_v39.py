from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakReversal(Strategy):
    METADATA = {
        "name": "BtcStreakReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 6
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.bars_in_pos = 0
        self.max_hold_bars = 4

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        opens = ctx.opens(50)
        if len(closes) < 30:
            return None

        # Manage active position exits
        if ctx.has_position():
            self.bars_in_pos += 1
            pos_dir = ctx.position_direction()
            current_bar_green = ctx.bar.close > ctx.bar.open
            current_bar_red = ctx.bar.close < ctx.bar.open

            # Snap exit: first opposing candle or reached max hold horizon
            if pos_dir == "long" and (current_bar_green or self.bars_in_pos >= self.max_hold_bars):
                reason = "streak_snap_green" if current_bar_green else "time_stop_exhaustion"
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "bars_held": self.bars_in_pos})
            
            if pos_dir == "short" and (current_bar_red or self.bars_in_pos >= self.max_hold_bars):
                reason = "streak_snap_red" if current_bar_red else "time_stop_exhaustion"
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "bars_held": self.bars_in_pos})

            return None

        # Reset count if no position
        self.bars_in_pos = 0

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter to address prior failure (avoid trading in crisis or runaway meltdown)
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Count consecutive same-color completed bars (checking backwards from most recent bar)
        streak_down = 0
        streak_up = 0
        for i in range(len(closes) - 1, -1, -1):
            c = closes[i]
            o = opens[i]
            if c < o:
                if streak_up > 0:
                    break
                streak_down += 1
            elif c > o:
                if streak_down > 0:
                    break
                streak_up += 1
            else:
                break

        rsi = self._rsi(closes, 14)

        # Long exhaustion entry: >=6 consecutive red bars + oversold regime
        if streak_down >= self.min_streak and rsi < 38.0:
            confidence = min(0.60 + (streak_down - self.min_streak) * 0.08, 0.95)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=280.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "exhaustion_downstreak_reversal",
                    "streak_len": streak_down,
                    "rsi": round(rsi, 2),
                    "regime": regime,
                }
            )

        # Short exhaustion entry: >=6 consecutive green bars + overbought regime
        if streak_up >= self.min_streak and rsi > 62.0:
            confidence = min(0.60 + (streak_up - self.min_streak) * 0.08, 0.95)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=280.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "exhaustion_upstreak_reversal",
                    "streak_len": streak_up,
                    "rsi": round(rsi, 2),
                    "regime": regime,
                }
            )

        return None