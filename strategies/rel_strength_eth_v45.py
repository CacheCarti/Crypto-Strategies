from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
        "required_features": ["eth_return_pct", "btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.4
        self.reversion_threshold = 0.5
        self.cooldown_period = 10
        self.last_exit_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(60)
        if len(closes) < 60:
            return None

        # Feature reads
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")

        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_abort", "regime": regime}
                )
            return None

        spread = eth_ret - btc_ret
        ema_fast = self._ema(closes, 20)
        ema_slow = self._ema(closes, 50)
        rsi = self._rsi(closes, 14)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and spread <= self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_spread_reverted",
                        "spread": spread,
                        "eth_ret": eth_ret,
                        "btc_ret": btc_ret,
                        "rsi": rsi,
                    }
                )
            elif direction == "short" and spread >= -self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_spread_reverted",
                        "spread": spread,
                        "eth_ret": eth_ret,
                        "btc_ret": btc_ret,
                        "rsi": rsi,
                    }
                )
            return None

        # Mandatory post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Long Setup: ETH significantly outperforming BTC + trend confirmation
        if (
            spread >= self.spread_threshold
            and current_price > ema_fast > ema_slow
            and 48.0 < rsi < 72.0
            and fg_index >= 30
        ):
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_breakout",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fear_greed": fg_index,
                }
            )

        # Short Setup: ETH significantly underperforming BTC + trend confirmation
        if (
            spread <= -self.spread_threshold
            and current_price < ema_fast < ema_slow
            and 28.0 < rsi < 52.0
            and fg_index <= 70
        ):
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_breakdown",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fear_greed": fg_index,
                }
            )

        return None