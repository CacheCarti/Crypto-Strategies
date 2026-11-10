from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEthBetaConvergence(Strategy):
    METADATA = {
        "name": "BTC-ETH Beta Convergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # 4-hour average hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_window = 5 # Bars for BTC momentum
        self.eth_window = 3 # Bars for ETH lagging
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.btc_window + 1)
        btc_returns = ctx.features.get("btc_return_pct", 0.0)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate recent BTC momentum (causal driver)
        # We need enough history for the current bar's BTC return to be recent
        if self.btc_window + 1 > len(ctx.closes(self.btc_window + 1)):
            return None

        # BTC has made a significant positive move
        if btc_returns > 0.015 and eth_returns < 0.005: # BTC up >1.5%, ETH up <0.5%
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "btc_leader_eth_lag_long",
                        "btc_24h_return_pct": btc_returns,
                        "eth_24h_return_pct": eth_returns,
                        "current_price": ctx.bar.close,
                    }
                )

        # BTC has made a significant negative move
        elif btc_returns < -0.015 and eth_returns > -0.005: # BTC down >1.5%, ETH down <0.5%
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "btc_leader_eth_lag_short",
                        "btc_24h_return_pct": btc_returns,
                        "eth_24h_return_pct": eth_returns,
                        "current_price": ctx.bar.close,
                    }
                )
        
        # Exit logic: if we have a position and the relationship normalizes
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if BTC return slows down or ETH catches up
                if btc_returns < 0.005 or eth_returns > 0.01:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_beta_normalized",
                            "btc_24h_return_pct": btc_returns,
                            "eth_24h_return_pct": eth_returns,
                            "current_price": ctx.bar.close,
                            "bars_held": ctx.bar_index - self.last_trade_bar,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if BTC return slows down or ETH catches up
                if btc_returns > -0.005 or eth_returns < -0.01:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_beta_normalized",
                            "btc_24h_return_pct": btc_returns,
                            "eth_24h_return_pct": eth_returns,
                            "current_price": ctx.bar.close,
                            "bars_held": ctx.bar_index - self.last_trade_bar,
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We update last_trade_bar on signal, but this can be used for logging or adaptive sizing
        pass