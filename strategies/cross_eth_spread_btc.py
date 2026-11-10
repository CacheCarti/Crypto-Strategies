from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEthSpreadFade(Strategy):
    METADATA = {
        "name": "BTC-ETH Spread Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20  # Period for Z-score calculation
        self.entry_zscore_threshold = 1.5  # Z-score deviation for entry
        self.exit_zscore_threshold = 0.5   # Z-score deviation for exit
        self.cooldown_bars = 5            # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Store historical spread data for Z-score calculation
        self.spread_history = []

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

        # Get 1-hour returns (current bar's return)
        btc_return = ctx.features.get("btc_return_pct", 0.0)
        eth_return = ctx.features.get("eth_return_pct", 0.0)

        # Calculate the spread: BTC return - ETH return
        # A positive spread means BTC outperformed ETH
        # A negative spread means ETH outperformed BTC
        current_spread = btc_return - eth_return
        self.spread_history.append(current_spread)

        # Keep history limited to the required period for Z-score
        if len(self.spread_history) > self.spread_period * 2: # Keep a bit more for robustness
            self.spread_history.pop(0)

        z_score = self._zscore(self.spread_history, self.spread_period)

        if z_score is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- Position Management ---
        if ctx.has_position():
            # Exit if spread normalizes (z-score close to zero)
            if ctx.position_direction() == "long": # Long BTC, short ETH spread (expect spread to decrease)
                if z_score < self.exit_zscore_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "spread_normalized_long_exit",
                        "current_zscore": z_score,
                        "exit_threshold": self.exit_zscore_threshold,
                        "btc_return": btc_return,
                        "eth_return": eth_return
                    })
            elif ctx.position_direction() == "short": # Short BTC, long ETH spread (expect spread to increase)
                if z_score > -self.exit_zscore_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "spread_normalized_short_exit",
                        "current_zscore": z_score,
                        "exit_threshold": -self.exit_zscore_threshold,
                        "btc_return": btc_return,
                        "eth_return": eth_return
                    })
            return None # Hold position if no exit condition met

        # --- Entry Logic ---
        # Long BTC / Short ETH spread: when BTC has significantly underperformed ETH (negative z-score)
        # We expect the spread to mean-revert upwards.
        if z_score < -self.entry_zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.7, metadata={
                "reason": "btc_underperformed_eth_spread_fade_long",
                "entry_zscore": z_score,
                "entry_threshold": -self.entry_zscore_threshold,
                "btc_return": btc_return,
                "eth_return": eth_return
            })

        # Short BTC / Long ETH spread: when BTC has significantly outperformed ETH (positive z-score)
        # We expect the spread to mean-revert downwards.
        elif z_score > self.entry_zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.7, metadata={
                "reason": "btc_outperformed_eth_spread_fade_short",
                "entry_zscore": z_score,
                "entry_threshold": self.entry_zscore_threshold,
                "btc_return": btc_return,
                "eth_return": eth_return
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log position close details or adapt parameters
        pass