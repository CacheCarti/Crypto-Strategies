from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEthSpreadFade(Strategy):
    METADATA = {
        "name": "BTC-ETH Spread Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for a 6-hour hold
        "warmup_bars": 60, # Need enough bars for 20-period std dev and EMA
        "required_features": ["eth_return_pct", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20 # Period for Z-score calculation
        self.zscore_threshold = 1.5 # Z-score threshold for entry
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate first trade

        # Z-score tracking for transition detection
        self.prev_zscore = 0.0

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

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        # We need historical returns for Z-score calculation
        # This requires storing returns or making a proxy
        # For simplicity, we'll use the current bar's return as the "latest" value
        # and assume ctx.features.get could give us a history if it were available.
        # Since it only gives current, we'll approximate by using closes to build a return history
        if len(ctx.closes(self.spread_period + 1)) < self.spread_period + 1:
            return None

        # Calculate historical BTC and ETH returns from closes
        btc_close_history = ctx.closes(self.spread_period + 1)
        eth_close_history = [c * (1 + (ctx.features.get("eth_return_pct", 0.0)/100)) for c in btc_close_history] # Proxy for ETH close history

        btc_daily_returns = [(btc_close_history[i] - btc_close_history[i-1]) / btc_close_history[i-1] * 100
                             for i in range(1, len(btc_close_history))]
        eth_daily_returns = [(eth_close_history[i] - eth_close_history[i-1]) / eth_close_history[i-1] * 100
                             for i in range(1, len(eth_close_history))]

        if len(btc_daily_returns) < self.spread_period or len(eth_daily_returns) < self.spread_period:
            return None

        # Leader-minus-target spread: BTC_return - ETH_return
        # We are using ETH as the leader, BTC as target, so: ETH - BTC
        spread_values = [e_r - b_r for e_r, b_r in zip(eth_daily_returns, btc_daily_returns)]

        current_spread = eth_returns - btc_returns
        spread_zscore = self._zscore(spread_values, self.spread_period)

        signal = None
        if not ctx.has_position():
            # Long BTC / Short ETH (if ETH outperforms BTC significantly)
            if spread_zscore > self.zscore_threshold and self.prev_zscore <= self.zscore_threshold:
                signal = ctx.signal(
                    "long",
                    confidence=min(0.9, (spread_zscore - self.zscore_threshold) / 2),
                    metadata={
                        "reason": "eth_btc_spread_fade_long",
                        "spread_zscore": spread_zscore,
                        "eth_return_pct": eth_returns,
                        "btc_return_pct": btc_returns,
                        "current_spread": current_spread
                    }
                )
            # Short BTC / Long ETH (if BTC outperforms ETH significantly)
            elif spread_zscore < -self.zscore_threshold and self.prev_zscore >= -self.zscore_threshold:
                signal = ctx.signal(
                    "short",
                    confidence=min(0.9, (-spread_zscore - self.zscore_threshold) / 2),
                    metadata={
                        "reason": "eth_btc_spread_fade_short",
                        "spread_zscore": spread_zscore,
                        "eth_return_pct": eth_returns,
                        "btc_return_pct": btc_returns,
                        "current_spread": current_spread
                    }
                )
        else:
            # Exit conditions: spread normalizes (Z-score close to zero)
            # Or if it moves against us significantly.
            if ctx.position_direction() == "long":
                # Exit long if spread normalizes or reverses
                if spread_zscore < 0.5 or spread_zscore < self.prev_zscore:
                    signal = ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "spread_normalized_or_reversed_long",
                            "spread_zscore": spread_zscore,
                            "eth_return_pct": eth_returns,
                            "btc_return_pct": btc_returns,
                            "current_spread": current_spread
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if spread normalizes or reverses
                if spread_zscore > -0.5 or spread_zscore > self.prev_zscore:
                    signal = ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "spread_normalized_or_reversed_short",
                            "spread_zscore": spread_zscore,
                            "eth_return_pct": eth_returns,
                            "btc_return_pct": btc_returns,
                            "current_spread": current_spread
                        }
                    )

        # Update previous z-score for transition detection
        self.prev_zscore = spread_zscore

        if signal:
            self.last_trade_bar = ctx.bar_index
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Use this for logging or adaptive sizing if needed
        # self.last_trade_bar = ctx.bar_index # Cooldown handled in on_bar
        pass