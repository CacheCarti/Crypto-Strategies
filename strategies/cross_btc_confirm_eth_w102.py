from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class CrossAssetTrendConfirm(Strategy):
    METADATA = {
        "name": "CrossAssetTrendConfirmETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_trend_period = 6
        self.eth_trend_period = 3
        self.min_btc_return_pct = 0.5 # require at least 0.5% move in BTC
        self.min_eth_return_pct = 0.3 # require at least 0.3% move in ETH
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)

        # Ensure we have enough recent data for fresh returns
        if abs(current_btc_return) < self.min_btc_return_pct or abs(current_eth_return) < self.min_eth_return_pct:
            return None # Not enough fresh movement

        # BTC as the leader for directional bias
        btc_is_bullish = current_btc_return > 0
        btc_is_bearish = current_btc_return < 0

        # ETH as the confirmation
        eth_is_bullish = current_eth_return > 0
        eth_is_bearish = current_eth_return < 0

        signal_direction = None
        reason = ""
        confidence = 0.0

        if not ctx.has_position():
            # Long entry condition: BTC is bullish AND ETH confirms bullish
            if btc_is_bullish and eth_is_bullish:
                signal_direction = "long"
                reason = "btc_eth_bullish_confirmation"
                confidence = min(1.0, (abs(current_btc_return) + abs(current_eth_return)) / (self.min_btc_return_pct + self.min_eth_return_pct) * 0.7) # Scale confidence based on strength
                self.last_signal_bar = ctx.bar_index

            # Short entry condition: BTC is bearish AND ETH confirms bearish
            elif btc_is_bearish and eth_is_bearish:
                signal_direction = "short"
                reason = "btc_eth_bearish_confirmation"
                confidence = min(1.0, (abs(current_btc_return) + abs(current_eth_return)) / (self.min_btc_return_pct + self.min_eth_return_pct) * 0.7)
                self.last_signal_bar = ctx.bar_index

        elif ctx.has_position():
            # Exit condition: Leader (BTC) disagrees with the current position, or ETH return normalizes (becomes flat)
            if ctx.position_direction() == "long":
                # Exit if BTC turns bearish or ETH return is flat/negative
                if btc_is_bearish or current_eth_return <= 0:
                    signal_direction = "flat"
                    reason = "btc_leader_disagreement_or_eth_normalization_long"
                    confidence = 0.7
                    self.last_signal_bar = ctx.bar_index
            elif ctx.position_direction() == "short":
                # Exit if BTC turns bullish or ETH return is flat/positive
                if btc_is_bullish or current_eth_return >= 0:
                    signal_direction = "flat"
                    reason = "btc_leader_disagreement_or_eth_normalization_short"
                    confidence = 0.7
                    self.last_signal_bar = ctx.bar_index

        if signal_direction:
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_btc_return_pct": current_btc_return,
                    "current_eth_return_pct": current_eth_return,
                    "eth_price": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy based on position close
        pass