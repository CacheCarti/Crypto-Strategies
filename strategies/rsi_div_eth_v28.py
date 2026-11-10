from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceEvent(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceEvent",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 8
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 12

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)

        if len(closes) < self.lookback + self.rsi_period:
            return None

        # Build RSI series for the lookback window
        rsi_series = []
        for i in range(self.lookback):
            idx = len(closes) - self.lookback + i + 1
            rsi_val = self._calc_rsi(closes[:idx], self.rsi_period)
            if rsi_val is None:
                return None
            rsi_series.append(rsi_val)

        curr_rsi = rsi_series[-1]
        bars_held = ctx.bar_index - self.entry_bar

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Time stop exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exit", "bars_held": bars_held, "rsi": curr_rsi}
                )

            # RSI normalization exit
            if pos_dir == "long" and curr_rsi >= 58.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_rsi_normalized", "rsi": curr_rsi}
                )
            if pos_dir == "short" and curr_rsi <= 42.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_rsi_normalized", "rsi": curr_rsi}
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Event detection: Find distinct local swing pivot across recent lookback
        # Look for pivot in range [-25, -6] to allow divergence maturation
        recent_lows = lows[-self.lookback:]
        recent_highs = highs[-self.lookback:]

        # Bullish Divergence check:
        # 1. Prior pivot low in bars 6 to 24 ago
        bull_pivot_idx = None
        min_p_val = float("inf")
        for i in range(5, 25):
            idx = self.lookback - 1 - i
            # Confirm local minimum with +/- 2 bar clearance
            if recent_lows[idx] == min(recent_lows[idx - 2: idx + 3]):
                if recent_lows[idx] < min_p_val:
                    min_p_val = recent_lows[idx]
                    bull_pivot_idx = idx

        if bull_pivot_idx is not None:
            prior_low_price = recent_lows[bull_pivot_idx]
            prior_low_rsi = rsi_series[bull_pivot_idx]
            curr_low_price = min(recent_lows[-3:])
            
            # Condition: Price makes a clear lower low, while RSI forms a higher low from oversold area
            if (curr_low_price < prior_low_price * 0.995 and 
                curr_rsi > prior_low_rsi + 2.5 and 
                prior_low_rsi < 35.0 and 
                curr_rsi < 45.0):
                
                self.last_trade_bar = ctx.bar_index
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_rsi_divergence",
                        "rsi": curr_rsi,
                        "prior_rsi": prior_low_rsi,
                        "curr_low": curr_low_price,
                        "prior_low": prior_low_price,
                    }
                )

        # Bearish Divergence check:
        bear_pivot_idx = None
        max_p_val = float("-inf")
        for i in range(5, 25):
            idx = self.lookback - 1 - i
            if recent_highs[idx] == max(recent_highs[idx - 2: idx + 3]):
                if recent_highs[idx] > max_p_val:
                    max_p_val = recent_highs[idx]
                    bear_pivot_idx = idx

        if bear_pivot_idx is not None:
            prior_high_price = recent_highs[bear_pivot_idx]
            prior_high_rsi = rsi_series[bear_pivot_idx]
            curr_high_price = max(recent_highs[-3:])

            # Condition: Price makes a higher high, while RSI forms a lower high from overbought area
            if (curr_high_price > prior_high_price * 1.005 and 
                curr_rsi < prior_high_rsi - 2.5 and 
                prior_high_rsi > 65.0 and 
                curr_rsi > 55.0):

                self.last_trade_bar = ctx.bar_index
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_rsi_divergence",
                        "rsi": curr_rsi,
                        "prior_rsi": prior_high_rsi,
                        "curr_high": curr_high_price,
                        "prior_high": prior_high_price,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index