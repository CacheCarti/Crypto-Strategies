from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalVolatility(Strategy):
    METADATA = {
        "name": "StreakReversalVolatility",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # 4 hours intended hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.max_hold_bars = 5

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
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
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

        if len(closes) < 25:
            return None

        # Check open position exit rules
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            is_green_bar = ctx.bar.close > ctx.bar.open
            is_red_bar = ctx.bar.close < ctx.bar.open
            
            # Snap exit: exit on the first reversal bar
            if pos_dir == "long" and is_green_bar:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "snap_green_exit", "close": ctx.bar.close, "open": ctx.bar.open}
                )
            if pos_dir == "short" and is_red_bar:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "snap_red_exit", "close": ctx.bar.close, "open": ctx.bar.open}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate streak of consecutive directional candles
        down_streak = 0
        up_streak = 0
        idx = len(closes) - 1
        while idx >= 0:
            if closes[idx] < opens[idx]:
                if up_streak > 0:
                    break
                down_streak += 1
            elif closes[idx] > opens[idx]:
                if down_streak > 0:
                    break
                up_streak += 1
            else:
                break
            idx -= 1

        if down_streak < self.min_streak and up_streak < self.min_streak:
            return None

        # Volatility & Momentum confirmation to fix prior failures:
        # Require terminal exhaustion candle expansion (current bar range > 1.2 * ATR)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None

        curr_range = ctx.bar.high - ctx.bar.low
        vol_expansion = curr_range >= (1.15 * atr)

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Long exhaustion setup: 5+ down bars, terminal expansion or deeply oversold RSI
        if down_streak >= self.min_streak and (vol_expansion or rsi < 32.0):
            conf = min(0.55 + 0.08 * (down_streak - self.min_streak), 0.90)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "streak_down_exhaustion",
                    "down_streak": down_streak,
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2),
                    "curr_range": round(curr_range, 2),
                }
            )

        # Short exhaustion setup: 5+ up bars, terminal expansion or overbought RSI
        if up_streak >= self.min_streak and (vol_expansion or rsi > 68.0):
            conf = min(0.55 + 0.08 * (up_streak - self.min_streak), 0.90)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "streak_up_exhaustion",
                    "up_streak": up_streak,
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2),
                    "curr_range": round(curr_range, 2),
                }
            )

        return None