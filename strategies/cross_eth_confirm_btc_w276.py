from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetMomentum(Strategy):
    METADATA = {
        "name": "CrossAssetMomentumBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_period = 3 # Lookback for ETH returns
        self.btc_period = 3 # Lookback for BTC returns
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # ETH (leader) recent returns
        eth_returns = [ctx.features.get("eth_return_pct", 0.0, offset=i) for i in range(self.eth_period)]
        # BTC (target) recent returns
        btc_returns = [ctx.market.get("btc_return_pct", 0.0, offset=i) for i in range(self.btc_period)]

        # Ensure we have enough data for returns
        if len(eth_returns) < self.eth_period or len(btc_returns) < self.btc_period:
            return None

        avg_eth_return = sum(eth_returns) / self.eth_period
        avg_btc_return = sum(btc_returns) / self.btc_period

        # Simple moving average for BTC trend confirmation
        sma_period = 20
        sma = self._sma(closes, sma_period)
        if sma is None:
            return None
        
        current_price = ctx.bar.close

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long condition: ETH showing recent positive momentum, BTC confirming, and BTC above SMA
        if avg_eth_return > 0.1 and avg_btc_return > 0.05 and current_price > sma:
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                confidence = min(0.5 + abs(avg_eth_return) * 2, 0.9) # Scale confidence by leader strength
                reason = "eth_leader_momentum_btc_confirm_sma_bull"
        
        # Short condition: ETH showing recent negative momentum, BTC confirming, and BTC below SMA
        elif avg_eth_return < -0.1 and avg_btc_return < -0.05 and current_price < sma:
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                confidence = min(0.5 + abs(avg_eth_return) * 2, 0.9)
                reason = "eth_leader_momentum_btc_confirm_sma_bear"

        # Exit condition: If leader (ETH) disagrees with current position direction or BTC trend breaks
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if ETH turns negative or BTC crosses below SMA
                if avg_eth_return < -0.05 or current_price < sma * 0.99: # 1% buffer for SMA cross
                    signal_direction = "flat"
                    reason = "exit_long_eth_disagreement_or_btc_sma_break"
                    confidence = 0.6
            elif ctx.position_direction() == "short":
                # Exit short if ETH turns positive or BTC crosses above SMA
                if avg_eth_return > 0.05 or current_price > sma * 1.01: # 1% buffer for SMA cross
                    signal_direction = "flat"
                    reason = "exit_short_eth_disagreement_or_btc_sma_break"
                    confidence = 0.6

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "avg_eth_return": avg_eth_return,
                    "avg_btc_return": avg_btc_return,
                    "current_price": current_price,
                    "sma_20": sma,
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset cooldown if needed (already handled by last_trade_bar)
        pass