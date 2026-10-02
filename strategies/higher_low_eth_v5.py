from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StructuralUptrendStaircase(Strategy):
    METADATA = {
        "name": "Structural Uptrend Staircase",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.swing_radius = 3
        self.lookback_bars = 42
        self.ema_trend_period = 21
        self.cooldown_bars = 5
        self.last_exit_bar = -100
        self.last_swing_low = 0.0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def _find_swing_lows(self, lows, radius=3, max_lookback=40):
        # Scan for local minimums with confirmation radius
        n = len(lows)
        swings = []
        start_idx = max(radius, n - max_lookback)
        end_idx = n - radius
        for i in range(start_idx, end_idx):
            val = lows[i]
            is_low = True
            for offset in range(-radius, radius + 1):
                if offset != 0 and lows[i + offset] <= val:
                    is_low = False
                    break
            if is_low:
                swings.append((i, val))
        return swings

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(55)
        lows = ctx.lows(55)
        highs = ctx.highs(55)
        opens = ctx.opens(55)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Filter crisis/meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "regime_emergency_exit", "regime": regime}
                )
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        ema_trend = self._ema(closes, self.ema_trend_period)
        rsi = self._rsi(closes, 14)

        if ema_trend is None or rsi is None:
            return None

        # Manage open position
        if ctx.has_position():
            # Invalidation: price breaks firmly below the structural swing low
            if self.last_swing_low > 0.0 and current_close < self.last_swing_low * 0.992:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "structure_breakdown",
                        "close": current_close,
                        "broken_swing_low": self.last_swing_low
                    }
                )

            # Overextended momentum take-profit
            if rsi > 78.0 and current_close < opens[-1]:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_overbought_exhaustion",
                        "rsi": round(rsi, 2),
                        "close": current_close
                    }
                )
            return None

        # Cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Detect structural swing lows in recent history
        swing_lows = self._find_swing_lows(lows, radius=self.swing_radius, max_lookback=self.lookback_bars)

        if len(swing_lows) >= 2:
            idx1, low1 = swing_lows[-2]
            idx2, low2 = swing_lows[-1]

            # Structural Staircase Check:
            # 1. Higher low formed (low2 > low1 with noticeable separation)
            # 2. Time separation between swing lows
            # 3. Current close above EMA trend filter
            # 4. Bullish confirmation on current bar (close > open and closing near highs)
            is_higher_low = low2 > low1 * 1.004
            sufficient_spacing = (idx2 - idx1) >= 4
            trend_aligned = current_close > ema_trend
            bullish_turn = (current_close > current_open) and (current_close > closes[-2])
            rsi_not_topped = 42.0 < rsi < 68.0

            if is_higher_low and sufficient_spacing and trend_aligned and bullish_turn and rsi_not_topped:
                self.last_swing_low = low2
                step_height_pct = round(((low2 - low1) / low1) * 100, 2)
                
                # Confidence scaling based on staircase slope and RSI support
                confidence = 0.65
                if current_close > highs[-2]:
                    confidence += 0.10
                if rsi > 50.0:
                    confidence += 0.05
                confidence = min(0.9, confidence)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "higher_low_staircase_bounce",
                        "low1": round(low1, 2),
                        "low2": round(low2, 2),
                        "step_height_pct": step_height_pct,
                        "ema21": round(ema_trend, 2),
                        "rsi": round(rsi, 2),
                        "close": current_close
                    }
                )

        return None