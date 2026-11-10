from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetEthLagBtc(Strategy):
    METADATA = {
        "name": "CrossAssetEthLagBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24  # Lookback for recent returns (24 hours)
        self.cooldown_bars = 6    # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Ensure immediate trading on first opportunity
        self.last_eth_signal_direction = None # To detect transitions
        self.eth_signal_threshold = 0.005 # 0.5% move in ETH

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)
        btc_returns = ctx.features.get("btc_return_pct", 0.0)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate average ETH return over a shorter period (e.g., last 3 bars) for "freshness"
        # Since eth_return_pct is 24h, we need to infer a shorter-term move or use its delta
        # For this strategy, we'll use the provided 24h return directly but look for significant change
        # A simple way to detect "freshness" is to see if the current 24h return is significantly different
        # from the previous one, indicating a recent acceleration.
        # However, eth_return_pct is already a 24h return, so a direct threshold might be better for "decisive move"
        
        # We need a way to detect a "fresh decisive move" in ETH that hasn't fully propagated to BTC.
        # Let's use the 24h ETH return as the primary driver, but ensure it's "new" by checking its sign change or magnitude.
        
        # Condition 1: ETH makes a decisive move (e.g., > 0.5% or < -0.5% in 24h)
        # Condition 2: BTC has not "caught up" yet (i.e., BTC return is less in magnitude and same direction)
        # Condition 3: We look for a transition in ETH's decisive move to avoid firing every bar.

        signal = None
        current_eth_signal = None

        if eth_returns > self.eth_signal_threshold: # ETH strong positive move
            current_eth_signal = "long"
            if self.last_eth_signal_direction != "long" and btc_returns < eth_returns * 0.5: # BTC lags
                # Long signal
                signal = ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_leader_btc_lag_long",
                        "eth_return_pct": eth_returns,
                        "btc_return_pct": btc_returns,
                        "signal_threshold": self.eth_signal_threshold,
                        "current_price": current_price
                    }
                )
        elif eth_returns < -self.eth_signal_threshold: # ETH strong negative move
            current_eth_signal = "short"
            if self.last_eth_signal_direction != "short" and btc_returns > eth_returns * 0.5: # BTC lags (less negative)
                # Short signal
                signal = ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_leader_btc_lag_short",
                        "eth_return_pct": eth_returns,
                        "btc_return_pct": btc_returns,
                        "signal_threshold": self.eth_signal_threshold,
                        "current_price": current_price
                    }
                )
        else:
            current_eth_signal = None # ETH is not making a decisive move

        # Update last_eth_signal_direction for transition detection
        self.last_eth_signal_direction = current_eth_signal

        if signal:
            self.last_trade_bar = ctx.bar_index
            return signal
        
        # If we have a position, check for normalization or time stop (engine handles time stop)
        # We can define "normalization" as BTC catching up or ETH reversing
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Exit if ETH reverses significantly or BTC has caught up enough
            if pos_direction == "long":
                if eth_returns < 0.001 and btc_returns > eth_returns * 0.9: # ETH return fades or BTC nearly caught up
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "long_position_eth_fade_btc_catchup", "eth_return_pct": eth_returns, "btc_return_pct": btc_returns, "current_price": current_price})
            elif pos_direction == "short":
                if eth_returns > -0.001 and btc_returns < eth_returns * 0.9: # ETH return fades or BTC nearly caught up
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "short_position_eth_fade_btc_catchup", "eth_return_pct": eth_returns, "btc_return_pct": btc_returns, "current_price": current_price})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state or log for adaptive sizing if needed
        pass