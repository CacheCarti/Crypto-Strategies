from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceSelective(Strategy):
    METADATA = {
        "name": "ETH RSI Divergence Selective",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 8
        self.last_trade_bar = -999

    def _rsi_series(self, closes, period=14):
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsis = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rs = 100.0 if avg_loss == 0.0 else avg_gain / avg_loss
        rsis.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            rs = 100.0 if avg_loss == 0.0 else avg_gain / avg_loss
            rsis.append(100.0 - (100.0 / (1.0 + rs)))
        return rsis

    def _find_pivots(self, prices, rsis, lookback=28):
        # Scan for swing low and swing high pivots with 2-bar confirmation on each side
        low_pivots = []
        high_pivots = []
        start_idx = max(2, len(prices) - lookback)
        end_idx = len(prices) - 2

        for i in range(start_idx, end_idx):
            # Pivot Low
            if prices[i] < prices[i-1] and prices[i] < prices[i-2] and prices[i] <= prices[i+1] and prices[i] < prices[i+2]:
                low_pivots.append((i, prices[i], rsis[i]))
            # Pivot High
            if prices[i] > prices[i-1] and prices[i] > prices[i-2] and prices[i] >= prices[i+1] and prices[i] > prices[i+2]:
                high_pivots.append((i, prices[i], rsis[i]))

        return low_pivots, high_pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        rsis = self._rsi_series(closes, self.rsi_period)
        if len(rsis) < self.lookback:
            return None

        current_rsi = rsis[-1]

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_rsi >= 58.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_long_exit", "rsi": current_rsi})
            elif pos_dir == "short" and current_rsi <= 42.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_short_exit", "rsi": current_rsi})
            return None

        # Filter out extreme crisis regimes
        if ctx.regime == "crisis":
            return None

        low_pivots, high_pivots = self._find_pivots(closes, rsis, self.lookback)

        # Bullish Divergence check: Price lower low, RSI higher low
        if len(low_pivots) >= 2:
            p1 = low_pivots[-2]  # older pivot
            p2 = low_pivots[-1]  # newer pivot
            # Spacing between pivots must be distinct (>= 4 bars)
            if (p2[0] - p1[0]) >= 4 and p2[1] < p1[1] and p2[2] > (p1[2] + 1.5):
                # Ensure the setup is in an oversold context to reject marginal traps
                if p1[2] < 38.0 and current_rsi < 48.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.8,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "p1_price": p1[1],
                            "p2_price": p2[1],
                            "p1_rsi": p1[2],
                            "p2_rsi": p2[2],
                        }
                    )

        # Bearish Divergence check: Price higher high, RSI lower high
        if len(high_pivots) >= 2:
            p1 = high_pivots[-2]  # older pivot
            p2 = high_pivots[-1]  # newer pivot
            if (p2[0] - p1[0]) >= 4 and p2[1] > p1[1] and p2[2] < (p1[2] - 1.5):
                # Ensure the setup is in an overbought context
                if p1[2] > 62.0 and current_rsi > 52.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.8,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "p1_price": p1[1],
                            "p2_price": p2[1],
                            "p1_rsi": p1[2],
                            "p2_rsi": p2[2],
                        }
                    )

        return None