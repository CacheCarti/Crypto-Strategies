from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 14400,  # ~4 hours hold for 1h swing
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0  # 2.0% return divergence
        self.cooldown_period = 6     # Wait 6 bars (6 hours) after exit before re-entering
        self.last_exit_bar = -999
        self.ema_period = 20

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature inputs
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Technical context on ETH (1h bars)
        ema20 = self._ema(closes, self.ema_period)
        if ema20 is None:
            return None
        current_close = closes[-1]

        # Manage existing position: exit when relative strength mean-reverts back through 0
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "eth_outperformance_reverted", "spread": spread, "close": current_close}
                )
            elif direction == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "eth_underperformance_reverted", "spread": spread, "close": current_close}
                )
            return None

        # Enforce cooldown to prevent overtrading and bleeding to friction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Filter out extreme crisis regimes to avoid high-slippage gap liquidations
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.7:
            return None

        # Long: ETH leading BTC by > 2% AND supported above medium-term trend
        if spread > self.spread_threshold and current_close > ema20:
            confidence = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_lead",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "close": current_close
                }
            )

        # Short: ETH lagging BTC by > 2% AND breaking below medium-term trend
        if spread < -self.spread_threshold and current_close < ema20:
            confidence = min(0.85, 0.55 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_lag",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "close": current_close
                }
            )

        return None