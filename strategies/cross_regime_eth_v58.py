from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.spread_threshold = 2.4  # Return % divergence trigger
        self.max_hold_bars = 16

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 30:
            return None

        # Check market crisis filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos = ctx.position or {}
            entry_price = pos.get("entry_price", ctx.bar.close)
            direction = ctx.position_direction()
            
            # Simple reversion exit if spread normalizes
            btc_ret = ctx.features.get("btc_return_pct", 0.0)
            eth_ret = ctx.features.get("eth_return_pct", 0.0)
            spread = eth_ret - btc_ret

            if direction == "long" and spread > 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_long", "spread": spread})
            elif direction == "short" and spread < 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_short", "spread": spread})
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate Divergence Feature
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Positive means ETH outrunning BTC, Negative means ETH lagging

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Catch-up Long: BTC is up (or steady), but ETH is lagging significantly + RSI not overbought
        # spread <= -threshold means eth_ret is materially below btc_ret
        if spread <= -self.spread_threshold and rsi < 55.0 and btc_ret > -1.5:
            conf = min(0.9, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                },
            )

        # Overextended Short: ETH outran BTC materially + RSI confirms overbought condition
        # spread >= threshold means eth_ret is materially above btc_ret
        if spread >= self.spread_threshold and rsi > 48.0 and eth_ret > 1.5:
            conf = min(0.9, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_spread_reversion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                },
            )

        return None