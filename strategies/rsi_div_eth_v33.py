from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_until_bar = 0
        self.entry_bar = 0

    def _calculate_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
        losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rsi_series = []
        rs = avg_gain / avg_loss if avg_loss > 1e-9 else 100.0
        rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss < 1e-9:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
                
        return rsi_series

    def _find_pivots_low(self, lows: List[float], rsi_vals: List[float], lookback: int = 35) -> Optional[Tuple[int, int]]:
        pivots = []
        start_idx = max(2, len(lows) - lookback)
        for i in range(start_idx, len(lows) - 2):
            if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] <= lows[i + 1] and lows[i] <= lows[i + 2]:
                if rsi_vals[i] < 42.0:
                    pivots.append(i)
        if len(pivots) >= 2:
            return pivots[-2], pivots[-1]
        return None

    def _find_pivots_high(self, highs: List[float], rsi_vals: List[float], lookback: int = 35) -> Optional[Tuple[int, int]]:
        pivots = []
        start_idx = max(2, len(highs) - lookback)
        for i in range(start_idx, len(highs) - 2):
            if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] >= highs[i + 1] and highs[i] >= highs[i + 2]:
                if rsi_vals[i] > 58.0:
                    pivots.append(i)
        if len(pivots) >= 2:
            return pivots[-2], pivots[-1]
        return None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        if len(closes) < 45:
            return None

        rsi_series = self._calculate_rsi_series(closes, 14)
        if not rsi_series or len(rsi_series) < 30:
            return None
        curr_rsi = rsi_series[-1]

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            if direction == "long" and (curr_rsi >= 60.0 or bars_held >= 10):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_long_exit", "rsi": curr_rsi, "bars_held": bars_held})
            if direction == "short" and (curr_rsi <= 40.0 or bars_held >= 10):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_short_exit", "rsi": curr_rsi, "bars_held": bars_held})
            return None

        # Check cooldown
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        lows = ctx.lows(45)
        highs = ctx.highs(45)
        rsi_aligned = [50.0] * 14 + rsi_series

        # Detect Bullish Divergence (lower low in price, higher low in RSI)
        piv_lows = self._find_pivots_low(lows, rsi_aligned, lookback=30)
        if piv_lows is not None:
            p1, p2 = piv_lows
            if (len(lows) - 1 - p2) <= 3:
                if lows[p2] < lows[p1] and rsi_aligned[p2] > rsi_aligned[p1] + 2.5:
                    if curr_rsi < 45.0:
                        self.entry_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bullish_rsi_divergence",
                                "p1_low": lows[p1],
                                "p2_low": lows[p2],
                                "p1_rsi": rsi_aligned[p1],
                                "p2_rsi": rsi_aligned[p2],
                                "rsi": curr_rsi
                            }
                        )

        # Detect Bearish Divergence (higher high in price, lower high in RSI)
        piv_highs = self._find_pivots_high(highs, rsi_aligned, lookback=30)
        if piv_highs is not None:
            p1, p2 = piv_highs
            if (len(highs) - 1 - p2) <= 3:
                if highs[p2] > highs[p1] and rsi_aligned[p2] < rsi_aligned[p1] - 2.5:
                    if curr_rsi > 55.0:
                        self.entry_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bearish_rsi_divergence",
                                "p1_high": highs[p1],
                                "p2_high": highs[p2],
                                "p1_rsi": rsi_aligned[p1],
                                "p2_rsi": rsi_aligned[p2],
                                "rsi": curr_rsi
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4