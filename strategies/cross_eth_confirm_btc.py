from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetEthConfirmBtc(Strategy):
    METADATA = {
        "name": "CrossAssetEthConfirmBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.fast_ema_period = 10
        self.slow_ema_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate EMAs for BTC
        btc_fast_ema = self._ema(closes, self.fast_ema_period)
        btc_slow_ema = self._ema(closes, self.slow_ema_period)

        if btc_fast_ema is None or btc_slow_ema is None:
            return None

        current_btc_close = closes[-1]
        previous_btc_close = closes[-2]

        # Get cross-asset returns
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        if not ctx.has_position():
            # Long entry conditions
            # ETH shows positive momentum, BTC is also positive and crossing EMAs
            if eth_return_pct > 0.05 and btc_return_pct > 0.03: # ETH leads with strong positive return, BTC confirms
                if btc_fast_ema > btc_slow_ema and previous_btc_close < btc_slow_ema and current_btc_close > btc_slow_ema: # BTC close confirms trend
                    signal_direction = "long"
                    confidence = min(1.0, (eth_return_pct + btc_return_pct) / 0.1) # Confidence based on strength of returns
                    reason = "cross_asset_eth_lead_btc_ema_confirm_long"
                    metadata = {
                        "eth_return_pct": eth_return_pct,
                        "btc_return_pct": btc_return_pct,
                        "btc_fast_ema": btc_fast_ema,
                        "btc_slow_ema": btc_slow_ema,
                        "current_btc_close": current_btc_close
                    }

            # Short entry conditions
            # ETH shows negative momentum, BTC is also negative and crossing EMAs
            elif eth_return_pct < -0.05 and btc_return_pct < -0.03: # ETH leads with strong negative return, BTC confirms
                if btc_fast_ema < btc_slow_ema and previous_btc_close > btc_slow_ema and current_btc_close < btc_slow_ema: # BTC close confirms trend
                    signal_direction = "short"
                    confidence = min(1.0, abs(eth_return_pct + btc_return_pct) / 0.1) # Confidence based on strength of returns
                    reason = "cross_asset_eth_lead_btc_ema_confirm_short"
                    metadata = {
                        "eth_return_pct": eth_return_pct,
                        "btc_return_pct": btc_return_pct,
                        "btc_fast_ema": btc_fast_ema,
                        "btc_slow_ema": btc_slow_ema,
                        "current_btc_close": current_btc_close
                    }

        elif ctx.has_position():
            # Exit conditions: relationship normalizes or reverses
            if ctx.position_direction() == "long":
                # Exit if ETH trend reverses or BTC EMAs cross negatively
                if eth_return_pct < -0.02 or btc_fast_ema < btc_slow_ema:
                    signal_direction = "flat"
                    confidence = 0.7
                    reason = "cross_asset_eth_reversal_or_btc_ema_cross_exit_long"
                    metadata = {
                        "eth_return_pct": eth_return_pct,
                        "btc_fast_ema": btc_fast_ema,
                        "btc_slow_ema": btc_slow_ema,
                        "current_btc_close": current_btc_close
                    }
            elif ctx.position_direction() == "short":
                # Exit if ETH trend reverses or BTC EMAs cross positively
                if eth_return_pct > 0.02 or btc_fast_ema > btc_slow_ema:
                    signal_direction = "flat"
                    confidence = 0.7
                    reason = "cross_asset_eth_reversal_or_btc_ema_cross_exit_short"
                    metadata = {
                        "eth_return_pct": eth_return_pct,
                        "btc_fast_ema": btc_fast_ema,
                        "btc_slow_ema": btc_slow_ema,
                        "current_btc_close": current_btc_close
                    }

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy
        pass