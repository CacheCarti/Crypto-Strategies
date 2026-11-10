from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # 4 hours intended hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.spread_threshold = 1.4  # Return divergence threshold in %
        self.rsi_period = 14

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Post-exit cooldown gate to prevent overtrading and fee erosion
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Positive if ETH outperforms, negative if ETH lags

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Filter out extreme crisis regimes to prevent toxic catch-up flow
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Dynamic exit condition when spread normalizes back to parity
        if has_pos:
            if pos_dir == "long" and spread >= -0.15:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "spread_reverted_flat_long",
                        "spread": spread,
                        "rsi": rsi,
                        "btc_ret": btc_ret,
                        "eth_ret": eth_ret,
                    }
                )
            elif pos_dir == "short" and spread <= 0.15:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "spread_reverted_flat_short",
                        "spread": spread,
                        "rsi": rsi,
                        "btc_ret": btc_ret,
                        "eth_ret": eth_ret,
                    }
                )
            return None

        # Entry logic: Catch-up Long (ETH underperformed BTC materially, local RSI not overbought)
        if spread <= -self.spread_threshold and rsi < 58.0:
            conf = min(0.9, 0.55 + abs(spread) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lag_catchup_long",
                    "spread": spread,
                    "rsi": rsi,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "crisis_score": crisis_score,
                }
            )

        # Entry logic: Mean-Reversion Short (ETH outran BTC materially, local RSI not deeply oversold)
        if spread >= self.spread_threshold and rsi > 42.0:
            conf = min(0.9, 0.55 + spread * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrun_mean_reversion_short",
                    "spread": spread,
                    "rsi": rsi,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "crisis_score": crisis_score,
                }
            )

        return None