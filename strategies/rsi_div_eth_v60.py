from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 3  # pivot confirmed if lowest/highest among +/- 3 bars
        self.cooldown_bars = 6
        self.last_trade_bar = -100

    def _calc_rsi_series(self, closes: list) -> list:
        period = self.rsi_period
        n = len(closes)
        if n < period + 1:
            return []
        
        rsi_series = [50.0] * n
        gains = [0.0] * n
        losses = [0.0] * n

        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            gains[i] = diff if diff > 0 else 0.0
            losses[i] = -diff if diff < 0 else 0.0

        avg_gain = sum(gains[1:period + 1]) / period
        avg_loss = sum(losses[1:period + 1]) / period

        if avg_loss == 0.0:
            rsi_series[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_series[period] = 100.0 - (100.0 / (1.0 + rs))

        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_series[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_series[i] = 100.0 - (100.0 / (1.0 + rs))

        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback + self.pivot_window + self.rsi_period + 2
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        rsi_series = self._calc_rsi_series(closes)
        if len(rsi_series) < total_needed:
            return None

        current_rsi = rsi_series[-1]
        pw = self.pivot_window

        # Position exit conditions: RSI normalizes or reverses
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_rsi >= 62.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_long_exit", "rsi": current_rsi})
            elif pos_dir == "short" and current_rsi <= 38.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_short_exit", "rsi": current_rsi})
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Crisp trigger: Pivot confirmation happens precisely at bar index: -1 - pw
        p_idx = len(closes) - 1 - pw
        p_close = closes[p_idx]
        p_rsi = rsi_series[p_idx]

        # Check if p_idx is a confirmed pivot low
        is_pivot_low = True
        for i in range(p_idx - pw, p_idx + pw + 1):
            if i != p_idx and closes[i] <= p_close:
                is_pivot_low = False
                break

        # Check if p_idx is a confirmed pivot high
        is_pivot_high = True
        for i in range(p_idx - pw, p_idx + pw + 1):
            if i != p_idx and closes[i] >= p_close:
                is_pivot_high = False
                break

        # Search window for previous pivot: lookback bars before p_idx
        start_search = max(pw + self.rsi_period, p_idx - self.lookback)

        # Bullish Divergence check (Lower Low in Price, Higher Low in RSI)
        if is_pivot_low and p_rsi < 38.0:
            for prev_idx in range(p_idx - pw - 1, start_search, -1):
                prev_close = closes[prev_idx]
                prev_is_low = True
                for j in range(prev_idx - pw, prev_idx + pw + 1):
                    if j != prev_idx and closes[j] <= prev_close:
                        prev_is_low = False
                        break
                
                if prev_is_low:
                    prev_rsi = rsi_series[prev_idx]
                    # Trigger condition: price lower low, RSI higher low (by at least 1.5 pts)
                    if p_close < prev_close and p_rsi > prev_rsi + 1.5:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.8,
                            stop_loss_bps=280.0,
                            take_profit_bps=520.0,
                            metadata={
                                "reason": "bullish_rsi_divergence",
                                "current_rsi": current_rsi,
                                "pivot_rsi": p_rsi,
                                "prev_pivot_rsi": prev_rsi,
                                "pivot_price": p_close,
                                "prev_pivot_price": prev_close,
                            }
                        )
                    break

        # Bearish Divergence check (Higher High in Price, Lower High in RSI)
        if is_pivot_high and p_rsi > 62.0:
            for prev_idx in range(p_idx - pw - 1, start_search, -1):
                prev_close = closes[prev_idx]
                prev_is_high = True
                for j in range(prev_idx - pw, prev_idx + pw + 1):
                    if j != prev_idx and closes[j] >= prev_close:
                        prev_is_high = False
                        break

                if prev_is_high:
                    prev_rsi = rsi_series[prev_idx]
                    # Trigger condition: price higher high, RSI lower high (by at least 1.5 pts)
                    if p_close > prev_close and p_rsi < prev_rsi - 1.5:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.8,
                            stop_loss_bps=280.0,
                            take_profit_bps=520.0,
                            metadata={
                                "reason": "bearish_rsi_divergence",
                                "current_rsi": current_rsi,
                                "pivot_rsi": p_rsi,
                                "prev_pivot_rsi": prev_rsi,
                                "pivot_price": p_close,
                                "prev_pivot_price": prev_close,
                            }
                        )
                    break

        return None