from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthSpreadReversion(Strategy):
    METADATA = {
        "name": "SOL/ETH Spread Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold of 4 hours
        "warmup_bars": 50,  # Need enough bars for 24h returns and EMA
        "required_features": ["sol_return_pct", "eth_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 1.25  # Percentage points (e.g., 1.25 means 1.25%)
        self.exit_spread_ratio = 0.5 # Exit when spread halves
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.regime_filter_active = True # Whether to use regime filters
        self.min_fear_greed = 30 # Only trade if F&G is not "Extreme Fear"
        self.max_fear_greed = 70 # Only trade if F&G is not "Extreme Greed"

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Calculate the spread (SOL return - ETH return)
        spread = sol_return_pct - eth_return_pct

        # Regime filtering
        if self.regime_filter_active:
            if not (self.min_fear_greed <= fear_greed_index <= self.max_fear_greed):
                return None # Stand down in extreme fear or greed

            # Avoid trading in "CRISIS" or "MELTDOWN" regimes
            if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
                return None

            # Avoid trading against strong trend regime (e.g., if market is strongly bearish, don't long)
            trend_regime = ctx.market.get("trend_regime", "neutral")
            trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
            if trend_confidence > 0.6: # Only consider confident trend signals
                if trend_regime == "bear" and spread > self.spread_threshold: # Don't short into a strong bear market
                    return None
                if trend_regime == "bull" and spread < -self.spread_threshold: # Don't long into a strong bull market
                    return None


        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        if ctx.has_position():
            position = ctx.position
            entry_spread = position["metadata"].get("entry_spread", 0.0)
            target_spread = entry_spread * self.exit_spread_ratio

            # Exit logic: spread halves or crosses zero
            if position["direction"] == "long":
                # Long: Entered when spread was very negative. Exit if spread becomes less negative (moves towards zero or positive)
                # or if it has halved its magnitude from entry.
                if spread >= target_spread or spread >= 0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_reversion_long_exit",
                        "entry_spread": entry_spread,
                        "current_spread": spread,
                        "target_spread": target_spread,
                        "sol_return_pct": sol_return_pct,
                        "eth_return_pct": eth_return_pct,
                        "fear_greed_index": fear_greed_index,
                        "regime": ctx.market.get("regime", "NORMAL")
                    })
            elif position["direction"] == "short":
                # Short: Entered when spread was very positive. Exit if spread becomes less positive (moves towards zero or negative)
                # or if it has halved its magnitude from entry.
                if spread <= target_spread or spread <= 0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "spread_reversion_short_exit",
                        "entry_spread": entry_spread,
                        "current_spread": spread,
                        "target_spread": target_spread,
                        "sol_return_pct": sol_return_pct,
                        "eth_return_pct": eth_return_pct,
                        "fear_greed_index": fear_greed_index,
                        "regime": ctx.market.get("regime", "NORMAL")
                    })
            return None # Hold position if exit conditions not met

        # Entry logic
        if spread > self.spread_threshold:
            # SOL significantly outperformed ETH, expect reversion (short SOL)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "sol_outperformed_eth_extreme_short_signal",
                "spread": spread,
                "threshold": self.spread_threshold,
                "sol_return_pct": sol_return_pct,
                "eth_return_pct": eth_return_pct,
                "fear_greed_index": fear_greed_index,
                "regime": ctx.market.get("regime", "NORMAL"),
                "entry_spread": spread
            })
        elif spread < -self.spread_threshold:
            # SOL significantly underperformed ETH, expect reversion (long SOL)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "sol_underperformed_eth_extreme_long_signal",
                "spread": spread,
                "threshold": -self.spread_threshold,
                "sol_return_pct": sol_return_pct,
                "eth_return_pct": eth_return_pct,
                "fear_greed_index": fear_greed_index,
                "regime": ctx.market.get("regime", "NORMAL"),
                "entry_spread": spread
            })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic implemented for this strategy yet.
        pass