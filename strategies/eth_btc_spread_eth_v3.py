from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional
import math

class EthBtcRelativeStrengthZScore(Strategy):
    METADATA = {
        "name": "ETH BTC Relative Strength Z-Score",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # Intended avg hold time is longer than 1 bar for mean reversion
        "warmup_bars": 61, # For Z-score period of 60 + 1 for previous bar comparison
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 60
        self.entry_zscore_threshold = 2.0
        self.cooldown_bars = 12 # 12 hours cooldown after an exit
        self.last_exit_bar_index = -self.cooldown_bars # Initialize to allow immediate entry

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Get historical return percentages
        eth_returns = [ctx.features.get("eth_return_pct", 0.0, i) for i in range(ctx.bar_index - self.zscore_period, ctx.bar_index + 1)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0, i) for i in range(ctx.bar_index - self.zscore_period, ctx.bar_index + 1)]

        if len(eth_returns) < self.zscore_period + 1 or len(btc_returns) < self.zscore_period + 1:
            return None

        # Calculate spread history
        spread_history = [e - b for e, b in zip(eth_returns, btc_returns)]

        # Ensure we have enough data for current and previous Z-score
        if len(spread_history) < self.zscore_period + 1:
            return None

        current_spread = spread_history[-1]
        previous_spread = spread_history[-2]

        # Calculate Z-score for current and previous bar
        current_sma = self._sma(spread_history, self.zscore_period)
        current_std = self._stddev(spread_history, self.zscore_period)

        # To get previous Z-score, we need SMA and STD up to the previous bar
        previous_sma = self._sma(spread_history[:-1], self.zscore_period)
        previous_std = self._stddev(spread_history[:-1], self.zscore_period)

        if current_sma is None or current_std is None or current_std == 0:
            return None
        if previous_sma is None or previous_std is None or previous_std == 0:
            # If previous std is 0, assume previous z-score was 0 for transition detection
            previous_zscore = 0.0
        else:
            previous_zscore = (previous_spread - previous_sma) / previous_std

        current_zscore = (current_spread - current_sma) / current_std

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar_index < self.cooldown_bars:
            return None

        # Entry logic
        if not ctx.has_position():
            # Long entry: ETH underperforming BTC, Z-score crosses below negative threshold
            if current_zscore < -self.entry_zscore_threshold and previous_zscore >= -self.entry_zscore_threshold:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "eth_btc_spread_oversold_zscore_cross",
                        "current_zscore": round(current_zscore, 2),
                        "previous_zscore": round(previous_zscore, 2),
                        "entry_threshold": self.entry_zscore_threshold,
                        "current_spread_bps": round(current_spread * 100, 2),
                        "current_price": ctx.bar.close,
                    }
                )
            # Short entry: ETH outperforming BTC, Z-score crosses above positive threshold
            elif current_zscore > self.entry_zscore_threshold and previous_zscore <= self.entry_zscore_threshold:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "eth_btc_spread_overbought_zscore_cross",
                        "current_zscore": round(current_zscore, 2),
                        "previous_zscore": round(previous_zscore, 2),
                        "entry_threshold": self.entry_zscore_threshold,
                        "current_spread_bps": round(current_spread * 100, 2),
                        "current_price": ctx.bar.close,
                    }
                )
        # Exit logic
        elif ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long: Spread normalizes (Z-score crosses above 0)
                if current_zscore > 0 and previous_zscore <= 0:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "eth_btc_spread_normalized_exit_long",
                            "current_zscore": round(current_zscore, 2),
                            "previous_zscore": round(previous_zscore, 2),
                            "current_spread_bps": round(current_spread * 100, 2),
                            "current_price": ctx.bar.close,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short: Spread normalizes (Z-score crosses below 0)
                if current_zscore < 0 and previous_zscore >= 0:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "eth_btc_spread_normalized_exit_short",
                            "current_zscore": round(current_zscore, 2),
                            "previous_zscore": round(previous_zscore, 2),
                            "current_spread_bps": round(current_spread * 100, 2),
                            "current_price": ctx.bar.close,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: dict) -> None:
        self.last_exit_bar_index = ctx.bar_index
        # Optional: Log position details or adapt strategy based on performance
        # ctx.log(f"Position closed: {position['direction']} at {position['exit_price']} for {position['return_bps']} bps return.")
        # ctx.log(f"Bars held: {position['bars_held']}")