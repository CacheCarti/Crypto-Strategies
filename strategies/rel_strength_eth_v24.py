from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.0
        self.ema_fast = 9
        self.ema_slow = 21

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        # Check cooldown after previous trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        rel_spread = eth_ret - btc_ret

        # Cross-asset trend and local volatility confirmation
        ema9 = self._ema(closes, self.ema_fast)
        ema21 = self._ema(closes, self.ema_slow)
        if ema9 is None or ema21 is None:
            return None

        current_close = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")

        # Avoid entering during disorderly meltdowns
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": regime}
                )
            return None

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean-reversion exit condition of the spread
            if direction == "long" and (rel_spread <= 0.2 or current_close < ema21):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_rel_strength_mean_reverted",
                        "spread": rel_spread,
                        "close": current_close,
                        "ema21": ema21,
                    }
                )
            elif direction == "short" and (rel_spread >= -0.2 or current_close > ema21):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_rel_strength_mean_reverted",
                        "spread": rel_spread,
                        "close": current_close,
                        "ema21": ema21,
                    }
                )
            return None

        # Entry logic: ETH significantly outperforming + technical alignment
        if rel_spread > self.spread_threshold and current_close > ema9 > ema21:
            confidence = min(0.9, 0.55 + (rel_spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_expansion",
                    "spread": rel_spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema9": ema9,
                    "ema21": ema21,
                }
            )

        # Entry logic: ETH significantly underperforming + technical alignment
        if rel_spread < -self.spread_threshold and current_close < ema9 < ema21:
            confidence = min(0.9, 0.55 + (abs(rel_spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_breakdown",
                    "spread": rel_spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema9": ema9,
                    "ema21": ema21,
                }
            )

        return None