from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcSpreadReversion(Strategy):
    METADATA = {
        "name": "SOL-BTC Spread Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 20
        self.zscore_threshold = 1.5
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

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
            return 0.0 # Avoid division by zero, treat as no deviation
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)

        # We need historical returns for z-score calculation
        # This is a bit tricky as ctx.features.get only gives current bar value.
        # We need to maintain a history of these returns.
        # For simplicity, let's assume `btc_return_pct` and `sol_return_pct`
        # are available for the last `spread_period` bars in some form.
        # Since the contract says ctx.features.get("key") gets the current bar's value,
        # we'll have to use `ctx.market.get("btc_return_pct")` and `ctx.market.get("sol_return_pct")`
        # for a proxy of historical returns. This is a compromise.
        # In a real system, these would be available as historical arrays.
        # For this exercise, we will assume the current bar values are sufficient for a simple spread.

        # Let's use the current bar's return for a simple spread, and rely on the
        # z-score over a period of *price* changes, not return changes.
        # This is because `btc_return_pct` and `sol_return_pct` are *daily* features,
        # and z-scoring them over 20 hourly bars isn't meaningful unless they are
        # themselves hourly returns. The problem statement says "btc_return_pct"
        # is a 24h return, so it's a slow feature.

        # Reinterpreting the strategy for backtestable data:
        # Instead of z-scoring the *return spread*, which is difficult with the given API
        # for historical feature values, let's z-score the *price ratio* of SOL/BTC.
        # This allows us to use `closes` for SOL and `btc_closes` (if available via market context or feature).
        # Since `btc_return_pct` is a feature, we can infer BTC price movement.
        # The prompt mentions "btc_return_pct as the causal driver" and "leader-minus-target return spread".

        # A more faithful implementation given API constraints:
        # We need to assess the "spread" between SOL and BTC performance.
        # Let's consider the divergence of SOL from BTC's movement.
        # We'll calculate a proxy for SOL's "expected" close based on BTC's return,
        # and then z-score the deviation of SOL's actual close from this expectation.

        # To avoid over-complexity with price ratios and external asset prices,
        # let's simplify to a Z-score of SOL's own price, but *gated* by BTC's recent movement.
        # The prompt emphasizes "cross-asset strategy" and "leader-minus-target return spread".
        # Let's define the "spread" as `SOL_return - BTC_return`.
        # We need a history of this spread to calculate its z-score.
        # The `ctx.features.get("btc_return_pct")` provides the *current* bar's 24h return.
        # We can't access `ctx.features.get("btc_return_pct", history=N)` directly.

        # The most robust way to interpret "cross-asset spread" with the given API
        # for backtestable data is to assume `btc_return_pct` and `sol_return_pct`
        # are *per-bar* returns, or that we can maintain a history.
        # Since they are stated as 24h returns, they are slow.

        # Let's use a simpler interpretation: Z-score of SOL's own price,
        # and then use BTC's recent movement as a confirmation/causal driver.

        # To align with "cross-asset strategy" and "leader-minus-target return spread",
        # let's create a synthetic spread by tracking the difference between
        # SOL's current price and a BTC-adjusted expected price.

        # This is still too complex given the constraints. Let's simplify and make it work.
        # The most direct interpretation of "z-score the leader-minus-target return spread"
        # that is implementable with `ctx.features.get` values, is to use a rolling window of
        # these *current* bar returns to approximate a spread history.
        # This means `self.all_btc_returns` and `self.all_sol_returns` should be maintained.

        # This requires storing state across bars, which is allowed.
        if not hasattr(self, 'all_btc_returns'):
            self.all_btc_returns = [0.0] * self.METADATA["warmup_bars"]
            self.all_sol_returns = [0.0] * self.METADATA["warmup_bars"]

        self.all_btc_returns.append(btc_returns)
        self.all_sol_returns.append(sol_returns)

        # Keep history to `spread_period` plus some buffer
        if len(self.all_btc_returns) > self.spread_period * 2:
            self.all_btc_returns.pop(0)
            self.all_sol_returns.pop(0)

        # Calculate the spread: SOL return - BTC return
        # We use current bar's 24h returns, which means the spread is also 24h.
        # Z-scoring a 24h return spread over 20 hourly bars is not ideal.
        # Let's use the *difference* in 24h returns as the spread.
        # This implies that when SOL has outperformed BTC significantly (positive spread), it's overextended relative to BTC.
        # When SOL has underperformed BTC significantly (negative spread), it's under-extended relative to BTC.

        # Let's redefine "spread" for this strategy:
        # The difference between SOL's price change and BTC's price change over a short period.
        # Since `btc_return_pct` and `sol_return_pct` are *24h* returns,
        # using them directly as a "spread" to z-score for a 1h bar strategy is problematic.
        # They will be very sticky.

        # Let's assume the user meant a spread based on *recent* price action.
        # We can calculate a short-term momentum for SOL and BTC.
        sol_closes = ctx.closes(self.spread_period + 1)
        btc_closes_proxy = [] # Need to derive BTC price from its return %
        # This is difficult without direct BTC price access.

        # Okay, let's revert to a simpler, but still cross-asset, approach that adheres to the API.
        # "z-score the leader-minus-target return spread and fade only extreme spread dislocations"
        # We have `btc_return_pct` and `sol_return_pct`. Let's assume they are comparable.
        # We will take the difference `sol_return_pct - btc_return_pct` as our spread.
        # We need a history of this spread.

        spread_history = [s_ret - b_ret for s_ret, b_ret in zip(self.all_sol_returns, self.all_btc_returns)]

        if len(spread_history) < self.spread_period:
            return None

        # Calculate z-score of this spread
        current_spread = sol_returns - btc_returns
        recent_spreads = spread_history[-self.spread_period:]

        # Manual Z-score calculation
        if len(recent_spreads) < self.spread_period:
            return None
        
        mean_spread = sum(recent_spreads) / len(recent_spreads)
        variance_spread = sum((x - mean_spread) ** 2 for x in recent_spreads) / len(recent_spreads)
        std_spread = math.sqrt(variance_spread)

        z_score_spread = 0.0
        if std_spread > 0:
            z_score_spread = (current_spread - mean_spread) / std_spread

        signal_confidence = 0.0

        if not ctx.has_position():
            # Fade extreme positive spread (SOL outperformed BTC too much) -> Short SOL
            if z_score_spread > self.zscore_threshold:
                signal_confidence = min(1.0, (z_score_spread - self.zscore_threshold) / 1.0) # Scale confidence
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "sol_btc_spread_overextended_short",
                        "z_score_spread": z_score_spread,
                        "current_sol_return_pct": sol_returns,
                        "current_btc_return_pct": btc_returns,
                        "spread_mean": mean_spread,
                        "spread_std": std_spread,
                    }
                )
            # Fade extreme negative spread (SOL underperformed BTC too much) -> Long SOL
            elif z_score_spread < -self.zscore_threshold:
                signal_confidence = min(1.0, (-z_score_spread - self.zscore_threshold) / 1.0) # Scale confidence
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "sol_btc_spread_underextended_long",
                        "z_score_spread": z_score_spread,
                        "current_sol_return_pct": sol_returns,
                        "current_btc_return_pct": btc_returns,
                        "spread_mean": mean_spread,
                        "spread_std": std_spread,
                    }
                )
        else:
            # Exit conditions
            # If the spread normalizes (z-score close to zero)
            if ctx.position_direction() == "short" and z_score_spread < 0.5:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "spread_normalized_exit_short",
                        "z_score_spread": z_score_spread,
                        "current_sol_return_pct": sol_returns,
                        "current_btc_return_pct": btc_returns,
                    }
                )
            elif ctx.position_direction() == "long" and z_score_spread > -0.5:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "spread_normalized_exit_long",
                        "z_score_spread": z_score_spread,
                        "current_sol_return_pct": sol_returns,
                        "current_btc_return_pct": btc_returns,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters
        pass