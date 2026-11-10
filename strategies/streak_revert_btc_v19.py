from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakReversal(Strategy):
    METADATA = {
        "name": "BtcStreakReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -100

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
        if len(closes) < 25 or len(opens) < 25:
            return None

        # Check existing position for streak snap exit (first opposite color bar)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            cur_close = ctx.bar.close
            cur_open = ctx.bar.open
            is_green = cur_close > cur_open
            is_red = cur_close < cur_open

            if (pos_dir == "long" and is_green) or (pos_dir == "short" and is_red):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "opposite_bar_snap_exit", "close": cur_close, "open": cur_open}
                )
            return None

        # Cooldown guard to avoid friction clustering
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate: avoid strong crisis meltdowns where streaks tend to cascade
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Count consecutive directional bars leading up to current bar
        up_streak = 0
        down_streak = 0
        n = len(closes)
        for i in range(n - 1, -1, -1):
            if closes[i] > opens[i]:
                if down_streak > 0:
                    break
                up_streak += 1
            elif closes[i] < opens[i]:
                if up_streak > 0:
                    break
                down_streak += 1
            else:
                break

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Long exhaustion: streak of red bars + RSI oversold confirmation (< 38)
        if down_streak >= self.min_streak and rsi < 38.0:
            conf = min(0.55 + 0.08 * (down_streak - self.min_streak), 0.90)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=350.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "streak_reversal_down_exhaustion",
                    "down_streak": down_streak,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                }
            )

        # Short exhaustion: streak of green bars + RSI overbought confirmation (> 62)
        if up_streak >= self.min_streak and rsi > 62.0:
            conf = min(0.55 + 0.08 * (up_streak - self.min_streak), 0.90)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=350.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "streak_reversal_up_exhaustion",
                    "up_streak": up_streak,
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                }
            )

        return None