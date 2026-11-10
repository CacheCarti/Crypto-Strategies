from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalExhaustion(Strategy):
    METADATA = {
        "name": "StreakReversalExhaustion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.bars_in_position = 0
        self.max_hold_bars = 5

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage active position: exit on first opposite bar or max hold duration
        if ctx.has_position():
            self.bars_in_position += 1
            pos_dir = ctx.position_direction()
            bar_color = "up" if ctx.bar.close > ctx.bar.open else ("down" if ctx.bar.close < ctx.bar.open else "flat")

            # Snap exit: long snaps on an up bar, short snaps on a down bar
            snap_exit = (pos_dir == "long" and bar_color == "up") or (pos_dir == "short" and bar_color == "down")
            timeout_exit = self.bars_in_position >= self.max_hold_bars

            if snap_exit or timeout_exit:
                reason = "streak_snap_take_profit" if snap_exit else "max_hold_timeout"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "bars_held": self.bars_in_position,
                        "close": ctx.bar.close,
                        "open": ctx.bar.open,
                    }
                )
            return None

        # Enforce cooldown after prior trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter extreme systemic crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Count consecutive directional bars directly preceding and including current bar
        down_streak = 0
        up_streak = 0

        # Scan backwards
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

        # Calculate momentum oscillator to verify exhaustion rather than runaway trend
        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / ctx.bar.close) * 10000.0 if ctx.bar.close > 0 else 50.0

        # Adjust SL/TP adaptively based on volatility
        sl_bps = max(150.0, min(350.0, atr_pct * 1.5))
        tp_bps = max(200.0, min(500.0, atr_pct * 2.2))

        # Long exhaustion setup: 5+ consecutive down bars and oversold / stretching
        if down_streak >= self.min_streak and rsi < 42.0:
            # Scale confidence with streak length (capped)
            conf = min(0.90, 0.55 + (down_streak - self.min_streak) * 0.08)
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                horizon_seconds=14400,
                metadata={
                    "reason": "streak_down_exhaustion_reversal",
                    "streak_count": down_streak,
                    "rsi": round(rsi, 2),
                    "atr_bps": round(atr_pct, 1),
                    "close": ctx.bar.close,
                }
            )

        # Short exhaustion setup: 5+ consecutive up bars and overbought / stretching
        if up_streak >= self.min_streak and rsi > 58.0:
            conf = min(0.90, 0.55 + (up_streak - self.min_streak) * 0.08)
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                horizon_seconds=14400,
                metadata={
                    "reason": "streak_up_exhaustion_reversal",
                    "streak_count": up_streak,
                    "rsi": round(rsi, 2),
                    "atr_bps": round(atr_pct, 1),
                    "close": ctx.bar.close,
                }
            )

        return None