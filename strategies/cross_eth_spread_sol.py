from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthSpreadFade(Strategy):
    METADATA = {
        "name": "SOL/ETH Spread Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20 # Period for z-score of spread
        self.zscore_threshold = 1.5 # How extreme the spread must be
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store historical spread data for z-score calculation
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
        if len(slice_v) < 2: # Need at least 2 points for std dev
            return 0.0
        variance = sum((x - mean) ** 2 for x in slice_v) / (len(slice_v) - 1) # Sample std dev
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

        # Get returns for the current bar
        sol_return = ctx.features.get("sol_return_pct", 0.0)
        eth_return = ctx.features.get("eth_return_pct", 0.0)

        # Calculate the spread: ETH return minus SOL return (leader minus target)
        # We are looking for dislocations where ETH moves significantly more/less than SOL
        current_spread = eth_return - sol_return
        self.spread_history.append(current_spread)

        # Keep history limited to the required period
        if len(self.spread_history) > self.spread_period * 2: # Keep a bit more for robustness
            self.spread_history.pop(0)

        if len(self.spread_history) < self.spread_period:
            return None

        # Calculate z-score of the spread
        spread_zscore = self._zscore(self.spread_history, self.spread_period)

        if ctx.has_position():
            # If we have a position, check if the spread has normalized or if it's time to exit
            if ctx.position_direction() == "long": # We are long SOL, meaning we shorted the spread (ETH was too high vs SOL)
                # Exit if spread returns to neutral or becomes negative
                if spread_zscore is not None and spread_zscore < 0.5: # Spread normalized
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_normalized_long_exit",
                        "current_spread_zscore": spread_zscore,
                        "eth_return_pct": eth_return,
                        "sol_return_pct": sol_return
                    })
            elif ctx.position_direction() == "short": # We are short SOL, meaning we longed the spread (ETH was too low vs SOL)
                # Exit if spread returns to neutral or becomes positive
                if spread_zscore is not None and spread_zscore > -0.5: # Spread normalized
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_normalized_short_exit",
                        "current_spread_zscore": spread_zscore,
                        "eth_return_pct": eth_return,
                        "sol_return_pct": sol_return
                    })
            return None # Hold position if no exit condition met

        # Entry logic (only if no position and cooldown passed)
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Fade extreme positive spread (ETH much higher than SOL) -> short SOL
        if spread_zscore is not None and spread_zscore > self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "extreme_positive_spread_fade_short_sol",
                "spread_zscore": spread_zscore,
                "zscore_threshold": self.zscore_threshold,
                "eth_return_pct": eth_return,
                "sol_return_pct": sol_return
            })

        # Fade extreme negative spread (ETH much lower than SOL) -> long SOL
        elif spread_zscore is not None and spread_zscore < -self.zscore_threshold:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "extreme_negative_spread_fade_long_sol",
                "spread_zscore": spread_zscore,
                "zscore_threshold": -self.zscore_threshold,
                "eth_return_pct": eth_return,
                "sol_return_pct": sol_return
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We can use this for logging or adaptive sizing, but for now, just reset cooldown is handled in on_bar.
        pass