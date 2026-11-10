from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "CrossAssetTrendConfirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_lookback = 3 # Bars for recent ETH trend
        self.btc_lookback = 3 # Bars for recent BTC trend
        self.cooldown_bars = 6 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.btc_lookback + 1)
        if len(closes) < self.btc_lookback + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.eth_lookback)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.btc_lookback)]

        # Ensure we have enough data for recent returns
        if len(eth_returns) < self.eth_lookback or len(btc_returns) < self.btc_lookback:
            return None

        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        current_eth_return = ctx.features.get("eth_return_pct", 0.0)

        # ETH as the causal driver: recent positive/negative trend
        eth_recent_trend = sum(eth_returns) / self.eth_lookback
        btc_recent_trend = sum(btc_returns) / self.btc_lookback

        # Thresholds for "agreement" and "confirmation"
        eth_trend_threshold = 0.05 # 0.05% average return over lookback
        btc_trend_threshold = 0.03 # 0.03% average return over lookback

        signal_confidence = 0.65

        # Long condition
        # ETH shows a clear positive trend AND BTC confirms with its own positive trend
        # AND current ETH return is fresh (positive)
        if (eth_recent_trend > eth_trend_threshold and
            btc_recent_trend > btc_trend_threshold and
            current_eth_return > 0.02): # Freshness: current ETH return also positive
            
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "eth_leader_btc_confirms_bullish",
                        "eth_recent_trend": eth_recent_trend,
                        "btc_recent_trend": btc_recent_trend,
                        "current_eth_return": current_eth_return,
                        "price": ctx.bar.close
                    }
                )
            elif ctx.position_direction() == "short":
                # Flip if strong signal in opposite direction
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence + 0.1, # Higher confidence for flip
                    metadata={
                        "reason": "flip_eth_leader_btc_confirms_bullish",
                        "eth_recent_trend": eth_recent_trend,
                        "btc_recent_trend": btc_recent_trend,
                        "current_eth_return": current_eth_return,
                        "price": ctx.bar.close
                    }
                )

        # Short condition
        # ETH shows a clear negative trend AND BTC confirms with its own negative trend
        # AND current ETH return is fresh (negative)
        elif (eth_recent_trend < -eth_trend_threshold and
              btc_recent_trend < -btc_trend_threshold and
              current_eth_return < -0.02): # Freshness: current ETH return also negative
            
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "eth_leader_btc_confirms_bearish",
                        "eth_recent_trend": eth_recent_trend,
                        "btc_recent_trend": btc_recent_trend,
                        "current_eth_return": current_eth_return,
                        "price": ctx.bar.close
                    }
                )
            elif ctx.position_direction() == "long":
                # Flip if strong signal in opposite direction
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence + 0.1, # Higher confidence for flip
                    metadata={
                        "reason": "flip_eth_leader_btc_confirms_bearish",
                        "eth_recent_trend": eth_recent_trend,
                        "btc_recent_trend": btc_recent_trend,
                        "current_eth_return": current_eth_return,
                        "price": ctx.bar.close
                    }
                )

        # Exit condition: Leader (ETH) disagrees with current position, or relationship normalizes
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            
            # Leader disagreement
            leader_disagrees_long = (pos_direction == "long" and eth_recent_trend < -eth_trend_threshold / 2)
            leader_disagrees_short = (pos_direction == "short" and eth_recent_trend > eth_trend_threshold / 2)

            # Relationship normalization (both trends near zero)
            normalization = (abs(eth_recent_trend) < eth_trend_threshold / 2 and
                             abs(btc_recent_trend) < btc_trend_threshold / 2)

            if leader_disagrees_long or leader_disagrees_short or normalization:
                self.last_trade_bar = ctx.bar_index
                reason = "leader_disagreement" if (leader_disagrees_long or leader_disagrees_short) else "relationship_normalization"
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": f"exit_{reason}",
                        "eth_recent_trend": eth_recent_trend,
                        "btc_recent_trend": btc_recent_trend,
                        "current_eth_return": current_eth_return,
                        "position_direction": pos_direction,
                        "price": ctx.bar.close
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset last trade bar for cooldown
        pass