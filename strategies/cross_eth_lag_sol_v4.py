from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSolBetaConvergence(Strategy):
    METADATA = {
        "name": "EthSolBetaConvergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_lookback = 3 # Lookback for ETH's decisive move
        self.sol_lookback = 3 # Lookback for SOL's relative underperformance
        self.min_eth_move_pct = 0.8 # ETH must move at least this much (e.g., 0.8% in 3 hours)
        self.max_sol_lag_pct = 0.3 # SOL can only move this much in the same direction
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        eth_returns = [ctx.features.get("eth_return_pct", 0.0)]
        # We need recent ETH returns to check for freshness
        # Since ctx.features.get("eth_return_pct") gives the current bar's 24h return,
        # we need to infer recent short-term moves from this or assume it's fresh enough.
        # For simplicity, we'll use the 24h return as a proxy for "recent decisive move"
        # and compare it to a recent SOL move.
        # A more precise implementation would require historical eth_return_pct or OHLC for ETH.
        # Given contract limitations, we use the current 24h return as the "leader move".

        current_eth_return_24h = ctx.features.get("eth_return_pct", 0.0)
        sol_recent_return = (ctx.bar.close - ctx.closes(self.sol_lookback)[0]) / ctx.closes(self.sol_lookback)[0] * 100.0 if len(ctx.closes(self.sol_lookback)) == self.sol_lookback else 0.0

        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        # Only trade in normal volatility and with a clear trend
        if market_regime == "CRISIS" or market_regime == "MELTDOWN":
            return None
        if trend_confidence < 0.6: # Require some confidence in the trend
            return None

        # Long condition: ETH up significantly, SOL lagging or flat
        if current_eth_return_24h > self.min_eth_move_pct and sol_recent_return < self.max_sol_lag_pct:
            if not ctx.has_position():
                if trend_regime == "bull": # Only long in a bull trend
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "eth_leads_sol_lag_long",
                            "eth_return_24h": current_eth_return_24h,
                            "sol_recent_return": sol_recent_return,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )
            elif ctx.position_direction() == "short": # Flip from short to long
                 if trend_regime == "bull":
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.8, # Higher confidence for a flip
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "eth_leads_sol_lag_long_flip",
                            "eth_return_24h": current_eth_return_24h,
                            "sol_recent_return": sol_recent_return,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )

        # Short condition: ETH down significantly, SOL lagging or flat
        elif current_eth_return_24h < -self.min_eth_move_pct and sol_recent_return > -self.max_sol_lag_pct:
            if not ctx.has_position():
                if trend_regime == "bear": # Only short in a bear trend
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "eth_leads_sol_lag_short",
                            "eth_return_24h": current_eth_return_24h,
                            "sol_recent_return": sol_recent_return,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )
            elif ctx.position_direction() == "long": # Flip from long to short
                if trend_regime == "bear":
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.8, # Higher confidence for a flip
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "eth_leads_sol_lag_short_flip",
                            "eth_return_24h": current_eth_return_24h,
                            "sol_recent_return": sol_recent_return,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )

        # Exit condition: If in a position and the leader-laggard relationship normalizes
        # Or if the trend reverses against us
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if ETH is no longer strongly positive or SOL has caught up
                if current_eth_return_24h < self.min_eth_move_pct * 0.5 or sol_recent_return > self.max_sol_lag_pct * 2.0:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "eth_sol_relationship_normalized_long_exit",
                            "eth_return_24h": current_eth_return_24h,
                            "sol_recent_return": sol_recent_return,
                        }
                    )
                # Exit long if trend becomes bearish or neutral
                if trend_regime == "bear" or (trend_regime == "neutral" and trend_confidence > 0.7):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "trend_reversed_against_long_exit",
                            "current_trend": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if ETH is no longer strongly negative or SOL has caught up
                if current_eth_return_24h > -self.min_eth_move_pct * 0.5 or sol_recent_return < -self.max_sol_lag_pct * 2.0:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "eth_sol_relationship_normalized_short_exit",
                            "eth_return_24h": current_eth_return_24h,
                            "sol_recent_return": sol_recent_return,
                        }
                    )
                # Exit short if trend becomes bullish or neutral
                if trend_regime == "bull" or (trend_regime == "neutral" and trend_confidence > 0.7):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "trend_reversed_against_short_exit",
                            "current_trend": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close
        self.last_signal_bar = ctx.bar_index
        pass