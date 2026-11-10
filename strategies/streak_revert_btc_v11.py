from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "BtcStreakExhaustionReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # ~4 hours expected max hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.bars_in_trade = 0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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

    def _zscore(self, values, period=20):
        if len(values) < period:
            return 0.0
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        opens = ctx.opens(35)
        if len(closes) < 35 or len(opens) < 35:
            return None

        # Manage open position
        if ctx.has_position():
            self.bars_in_trade += 1
            pos_dir = ctx.position_direction()
            curr_close = closes[-1]
            curr_open = opens[-1]

            # Snap exit: opposite color candle confirmed or time stop (4 bars)
            opposite_candle = (pos_dir == "long" and curr_close < curr_open) or \
                              (pos_dir == "short" and curr_close > curr_open)
            
            if opposite_candle or self.bars_in_trade >= 4:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "streak_snap_or_time_exit",
                        "bars_held": self.bars_in_trade,
                        "opposite_candle": opposite_candle
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme meltdown volatility where pure reversion is risky
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Count streak of consecutive same-color bars (excluding current bar)
        streak_down = 0
        streak_up = 0

        # Look back from bar index -1 downwards
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

        # Stat-arb / Volatility confirmation
        rsi_val = self._rsi(closes, 14)
        z_val = self._zscore(closes, 20)

        # Long exhaustion: 5+ down bars, oversold RSI, price stretched downwards
        if streak_down >= self.min_streak and rsi_val <= 38.0 and z_val <= -1.2:
            conf = min(0.9, 0.55 + (streak_down - self.min_streak) * 0.1)
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=350.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "down_streak_exhaustion_reversal",
                    "streak": streak_down,
                    "rsi": round(rsi_val, 2),
                    "zscore": round(z_val, 2)
                }
            )

        # Short exhaustion: 5+ up bars, overbought RSI, price stretched upwards
        if streak_up >= self.min_streak and rsi_val >= 62.0 and z_val >= 1.2:
            conf = min(0.9, 0.55 + (streak_up - self.min_streak) * 0.1)
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=350.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "up_streak_exhaustion_reversal",
                    "streak": streak_up,
                    "rsi": round(rsi_val, 2),
                    "zscore": round(z_val, 2)
                }
            )

        return None