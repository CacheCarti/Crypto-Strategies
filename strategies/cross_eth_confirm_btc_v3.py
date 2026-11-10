from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirm(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 30
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 1)
        if len(closes) < self.slow_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # BTC trend (target asset)
        btc_sma_fast = self._sma(closes, self.fast_period)
        btc_sma_slow = self._sma(closes, self.slow_period)
        
        if btc_sma_fast is None or btc_sma_slow is None:
            return None

        # ETH return (leader asset)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # BTC price action confirmation
        current_btc_close = ctx.bar.close
        prev_btc_close = closes[-2] # One bar before current

        if not ctx.has_position():
            # Long signal:
            # ETH shows strong positive momentum AND
            # BTC fast SMA crosses above slow SMA AND
            # BTC current close is higher than previous close (confirmation)
            if (eth_return_pct > 0.5 and # Strong positive ETH return
                btc_sma_fast > btc_sma_slow and
                self._sma(ctx.closes(self.fast_period + 1)[-self.fast_period-1:-1], self.fast_period) <= self._sma(ctx.closes(self.slow_period + 1)[-self.slow_period-1:-1], self.slow_period) and # Check for actual cross
                current_btc_close > prev_btc_close): # Price confirmation
                
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "cross_asset_eth_btc_bull_confirm",
                        "eth_return_pct": eth_return_pct,
                        "btc_sma_fast": btc_sma_fast,
                        "btc_sma_slow": btc_sma_slow,
                        "btc_close": current_btc_close,
                        "prev_btc_close": prev_btc_close
                    }
                )

            # Short signal:
            # ETH shows strong negative momentum AND
            # BTC fast SMA crosses below slow SMA AND
            # BTC current close is lower than previous close (confirmation)
            elif (eth_return_pct < -0.5 and # Strong negative ETH return
                  btc_sma_fast < btc_sma_slow and
                  self._sma(ctx.closes(self.fast_period + 1)[-self.fast_period-1:-1], self.fast_period) >= self._sma(ctx.closes(self.slow_period + 1)[-self.slow_period-1:-1], self.slow_period) and # Check for actual cross
                  current_btc_close < prev_btc_close): # Price confirmation
                
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "cross_asset_eth_btc_bear_confirm",
                        "eth_return_pct": eth_return_pct,
                        "btc_sma_fast": btc_sma_fast,
                        "btc_sma_slow": btc_sma_slow,
                        "btc_close": current_btc_close,
                        "prev_btc_close": prev_btc_close
                    }
                )
        else:
            # Exit logic:
            # If ETH return normalizes (close to zero) OR
            # BTC SMAs cross against the position direction (trend reversal)
            # OR BTC return % is going against us significantly
            is_long = ctx.position_direction() == "long"

            if is_long:
                if (abs(eth_return_pct) < 0.1 or # ETH return normalizes
                    btc_sma_fast < btc_sma_slow or # Trend reversal
                    btc_return_pct < -0.5): # BTC itself is falling
                    
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_eth_norm_or_btc_trend_rev",
                            "eth_return_pct": eth_return_pct,
                            "btc_sma_fast": btc_sma_fast,
                            "btc_sma_slow": btc_sma_slow,
                            "btc_return_pct": btc_return_pct
                        }
                    )
            else: # is_short
                if (abs(eth_return_pct) < 0.1 or # ETH return normalizes
                    btc_sma_fast > btc_sma_slow or # Trend reversal
                    btc_return_pct > 0.5): # BTC itself is rising
                    
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_eth_norm_or_btc_trend_rev",
                            "eth_return_pct": eth_return_pct,
                            "btc_sma_fast": btc_sma_fast,
                            "btc_sma_slow": btc_sma_slow,
                            "btc_return_pct": btc_return_pct
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass