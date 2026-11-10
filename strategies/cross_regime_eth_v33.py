from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Return Convergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.spread_threshold = 2.2
        self.rsi_period = 14

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

    def _zscore(self, values, period):
        if len(values) < period:
            return 0.0
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard to avoid churn and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Cross-asset returns (24h return percentage)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        
        # Spread: positive means ETH is outperforming BTC, negative means ETH is lagging BTC
        ret_spread = eth_ret - btc_ret

        # Avoid extreme meltdown/crisis whipsaws
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_risk_off", "crisis_score": crisis_score})
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Exit logic for open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion achieved: spread has normalized
            if pos_dir == "long" and (ret_spread >= 0.2 or rsi >= 65.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_normalized_or_overbought", "ret_spread": ret_spread, "rsi": rsi}
                )
            if pos_dir == "short" and (ret_spread <= -0.2 or rsi <= 35.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_normalized_or_oversold", "ret_spread": ret_spread, "rsi": rsi}
                )
            return None

        # Entry logic: ETH lagging BTC + local oversold confirmation
        if ret_spread <= -self.spread_threshold and rsi < 42.0:
            conf = min(0.9, max(0.5, 0.5 + abs(ret_spread) / 10.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "ret_spread": ret_spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                }
            )

        # Entry logic: ETH outrunning BTC + local overbought confirmation
        if ret_spread >= self.spread_threshold and rsi > 58.0:
            conf = min(0.9, max(0.5, 0.5 + abs(ret_spread) / 10.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "ret_spread": ret_spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                }
            )

        return None