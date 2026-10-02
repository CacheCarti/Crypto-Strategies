from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class HigherLowStructuralUptrend(Strategy):
    METADATA = {
        "name": "Higher Low Structural Uptrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 42
        self.ema_fast_period = 12
        self.rsi_period = 14
        self.cooldown_bars = 5
        self.last_exit_bar = -100
        self.active_structure_sl = 0.0

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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

    def _find_recent_swing_lows(self, lows: List[float], window: int = 40) -> List[Tuple[int, float]]:
        swing_lows = []
        n = len(lows)
        start_idx = max(2, n - window)
        end_idx = n - 2
        for i in range(start_idx, end_idx):
            val = lows[i]
            if val < lows[i - 1] and val < lows[i - 2] and val <= lows[i + 1] and val <= lows[i + 2]:
                swing_lows.append((i, val))
        return swing_lows

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.active_structure_sl = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 10)
        lows = ctx.lows(self.lookback_bars + 10)

        if len(closes) < self.lookback_bars + 10:
            return None

        current_price = ctx.bar.close
        ema_fast = self._ema(closes, self.ema_fast_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or rsi is None:
            return None

        # Position Management & Invalidation
        if ctx.has_position():
            # Invalidation exit: price breaks below the second swing low that formed structure
            if self.active_structure_sl > 0.0 and current_price < self.active_structure_sl:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "structural_lower_low_break",
                        "price": current_price,
                        "structure_sl": self.active_structure_sl,
                        "rsi": rsi,
                    },
                )
            # Overextended momentum exit
            if rsi > 78.0 and current_price > ema_fast:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_overbought_take_profit",
                        "price": current_price,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                    },
                )
            return None

        # Cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        if funding_rate > 0.0006:  # Overheated long crowding
            return None

        # Detect two consecutive higher swing lows in lookback window
        swing_lows = self._find_recent_swing_lows(lows, window=self.lookback_bars)
        if len(swing_lows) < 2:
            return None

        # Take the two most recent swing lows
        low1_idx, low1_val = swing_lows[-2]
        low2_idx, low2_val = swing_lows[-1]

        # Structural condition: second low is higher than first low with separation
        bars_between = low2_idx - low1_idx
        if bars_between < 5 or low2_val <= low1_val * 1.002:
            return None

        # Ensure the second swing low is relatively fresh (formed within last 8 bars)
        bars_since_low2 = (len(lows) - 1) - low2_idx
        if bars_since_low2 > 8 or bars_since_low2 < 2:
            return None

        # Trigger confirmation: price is turning up above EMA and RSI is healthy
        turned_up = current_price > ema_fast and current_price > ctx.opens(1)[-1]
        rsi_healthy = 42.0 < rsi < 68.0

        if turned_up and rsi_healthy:
            self.active_structure_sl = low2_val
            confidence = min(0.9, 0.65 + (low2_val / low1_val - 1.0) * 10.0)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "higher_low_stair_step_breakout",
                    "price": current_price,
                    "low1_val": low1_val,
                    "low2_val": low2_val,
                    "low_diff_pct": round((low2_val - low1_val) / low1_val * 100, 3),
                    "bars_between": bars_between,
                    "ema_fast": round(ema_fast, 2),
                    "rsi": round(rsi, 2),
                    "regime": regime,
                },
            )

        return None