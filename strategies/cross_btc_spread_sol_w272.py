from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcSpreadFade(Strategy):
    METADATA = {
        "name": "SOL/BTC Spread Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold of 4 hours
        "warmup_bars": 60,  # Need enough bars for Z-score and EMA
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 48  # 2 days of 1-hour bars
        self.ema_period = 12    # 12 hours for average spread
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars

        # Thresholds for Z-score to trigger entry
        self.long_zscore_threshold = -2.0
        self.short_zscore_threshold = 2.0

        # Confidence scaling based on market regime
        self.regime_confidence_boost = {
            "calm": 0.1,
            "normal": 0.0,
            "volatile": -0.1,
            "crisis": -0.2,
        }

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
        if mean is None or std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        sol_returns = ctx.features.get("sol_return_pct", 0.0)
        btc_returns = ctx.features.get("btc_return_pct", 0.0)

        # We need historical returns for z-score calculation
        # This assumes sol_return_pct and btc_return_pct are available for past bars
        # For a production system, these would need to be stored or derived from prices.
        # Here, we will reconstruct them from closes for simplicity and backtest compatibility.
        # This is a simplification, as ctx.features.get only gives current bar.
        # A more robust implementation would require storing historical feature values.
        # For this example, let's assume the platform provides historical feature values.
        # Since ctx.features.get only returns the current bar's value, we need to adapt.
        # Let's derive the spread from close prices.

        closes = ctx.closes(self.zscore_period + 1)
        if len(closes) < self.zscore_period + 1:
            return None

        # Calculate log returns for SOL and BTC from close prices for the spread
        # NOTE: This is a proxy for actual feature data. In a real system, we'd use the stored features.
        # Given the constraints, we'll use price ratios as a proxy for cross-asset behavior.
        # Let's consider the ratio of SOL price to BTC price (proxied by BTCUSD price).
        # We need BTCUSD prices, which are not directly available as a series in BarContext.
        # The prompt specifically mentions using btc_return_pct.
        # Since ctx.features.get("btc_return_pct") only gives the current bar's return,
        # we cannot compute a z-score of the spread based on historical feature values directly from BarContext.
        # This is a critical limitation for the "cross-asset z-score spread" logic as described.

        # REINTERPRETATION FOR BACKTESTABILITY:
        # Instead of z-scoring the *spread of returns*, let's z-score the *SOL returns conditioned by BTC returns*.
        # The core idea is "fade extreme SOL movements when BTC isn't moving as much".
        # This maintains the cross-asset and fade concept with backtestable data.

        # Let's define the "causal driver" as BTC's return.
        # The "target" asset is SOL.
        # We want to fade SOL when its movement is disproportionate to BTC's, assuming BTC is the leader.

        # Calculate a "normalized SOL return" by subtracting BTC's return.
        # This is the "spread" we're interested in.
        # `sol_minus_btc_returns` represents how much SOL moved relative to BTC.
        # If BTC moves +1% and SOL moves +3%, `sol_minus_btc_returns` is +2%.
        # If BTC moves +1% and SOL moves -1%, `sol_minus_btc_returns` is -2%.

        # To z-score this, we need a history of `sol_minus_btc_returns`.
        # This requires the strategy to store historical values.

        # Let's simplify: we will use the *current* `sol_minus_btc_returns` and compare it to a recent average.
        # This is not a true z-score of the *spread*, but a z-score of SOL's current deviation
        # from its recent average, accounting for BTC's current movement.

        # This requires storing historical values of `sol_minus_btc_returns`.
        # Let's manage a rolling list of these values.
        if not hasattr(self, 'sol_btc_spread_history'):
            self.sol_btc_spread_history = []

        # Calculate the current spread for the Z-score calculation
        current_spread = sol_returns - btc_returns
        self.sol_btc_spread_history.append(current_spread)

        # Keep history limited to zscore_period
        if len(self.sol_btc_spread_history) > self.zscore_period:
            self.sol_btc_spread_history.pop(0)

        if len(self.sol_btc_spread_history) < self.zscore_period:
            return None

        # Calculate Z-score of the current spread relative to its recent history
        spread_zscore = self._zscore(self.sol_btc_spread_history, self.zscore_period)

        # Calculate EMA of recent close prices for a trend filter or normalization
        ema_closes = ctx.closes(self.ema_period)
        if len(ema_closes) < self.ema_period:
            return None
        current_ema = self._ema(ema_closes, self.ema_period)
        current_price = ctx.bar.close

        base_confidence = 0.6
        regime_adjustment = self.regime_confidence_boost.get(ctx.regime, 0.0)
        confidence = max(0.1, min(0.9, base_confidence + regime_adjustment))

        # Check for long entry (SOL underperformed BTC significantly, expecting mean reversion)
        if spread_zscore < self.long_zscore_threshold and current_price > current_ema:
            # Only go long if price is above a short-term EMA, indicating some underlying strength
            # or at least not a strong downtrend that would fight mean reversion.
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_btc_spread_fade_long",
                    "spread_zscore": round(spread_zscore, 2),
                    "sol_return_pct": round(sol_returns, 2),
                    "btc_return_pct": round(btc_returns, 2),
                    "current_price": round(current_price, 2),
                    "ema_price": round(current_ema, 2),
                    "regime": ctx.regime,
                },
            )

        # Check for short entry (SOL outperformed BTC significantly, expecting mean reversion)
        elif spread_zscore > self.short_zscore_threshold and current_price < current_ema:
            # Only go short if price is below a short-term EMA, indicating some underlying weakness
            # or at least not a strong uptrend that would fight mean reversion.
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_btc_spread_fade_short",
                    "spread_zscore": round(spread_zscore, 2),
                    "sol_return_pct": round(sol_returns, 2),
                    "btc_return_pct": round(btc_returns, 2),
                    "current_price": round(current_price, 2),
                    "ema_price": round(current_ema, 2),
                    "regime": ctx.regime,
                },
            )

        # Exit logic: if a position is open and spread normalizes or reverses
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            entry_spread_zscore = ctx.position.get("metadata", {}).get("spread_zscore")
            
            # Exit if the spread has normalized (z-score close to zero)
            # Or if it has reversed significantly against the position
            if pos_direction == "long":
                if spread_zscore > -0.5: # Spread has moved back towards or above zero
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "spread_normalized_long_exit",
                            "current_spread_zscore": round(spread_zscore, 2),
                            "entry_spread_zscore": round(entry_spread_zscore, 2) if entry_spread_zscore else "N/A",
                            "sol_return_pct": round(sol_returns, 2),
                            "btc_return_pct": round(btc_returns, 2),
                            "current_price": round(current_price, 2),
                        },
                    )
                # Or if the price drops below the EMA (indicating trend reversal for the asset)
                if current_price < current_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "price_below_ema_long_exit",
                            "current_price": round(current_price, 2),
                            "ema_price": round(current_ema, 2),
                            "spread_zscore": round(spread_zscore, 2),
                        },
                    )
            elif pos_direction == "short":
                if spread_zscore < 0.5: # Spread has moved back towards or below zero
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "spread_normalized_short_exit",
                            "current_spread_zscore": round(spread_zscore, 2),
                            "entry_spread_zscore": round(entry_spread_zscore, 2) if entry_spread_zscore else "N/A",
                            "sol_return_pct": round(sol_returns, 2),
                            "btc_return_pct": round(btc_returns, 2),
                            "current_price": round(current_price, 2),
                        },
                    )
                # Or if the price rises above the EMA (indicating trend reversal for the asset)
                if current_price > current_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "price_above_ema_short_exit",
                            "current_price": round(current_price, 2),
                            "ema_price": round(current_ema, 2),
                            "spread_zscore": round(spread_zscore, 2),
                        },
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_trade_bar = ctx.bar_index
        # Optional: adaptive sizing or logging based on position performance
        pass