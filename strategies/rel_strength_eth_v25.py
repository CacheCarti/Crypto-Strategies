from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.4
        self.reversion_threshold = 0.5
        self.ema_filter_period = 24
        self.min_bars_held = 4

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
        closes = ctx.closes(self.ema_filter_period + 5)
        if len(closes) < self.ema_filter_period:
            return None

        # Feature spread computation
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Avoid entries in extreme crisis or illiquid panics
        if crisis_score > 0.65 or regime == "MELTDOWN":
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "risk_off_crisis_exit", "crisis_score": crisis_score}
                )
            return None

        current_close = ctx.bar.close
        ema = self._ema(closes, self.ema_filter_period)
        if ema is None:
            return None

        # Position management: Mean-reversion exit or trend break
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Long exit: spread normalizes back down or structure breaks
            if pos_dir == "long":
                if spread <= self.reversion_threshold or current_close < ema * 0.995:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted_or_structure_lost",
                            "spread": spread,
                            "ema": ema,
                            "close": current_close
                        }
                    )
            # Short exit: spread normalizes back up or structure breaks upward
            elif pos_dir == "short":
                if spread >= -self.reversion_threshold or current_close > ema * 1.005:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted_or_structure_lost",
                            "spread": spread,
                            "ema": ema,
                            "close": current_close
                        }
                    )
            return None

        # Entry gating: Mandatory post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Strong ETH outperformance + local trend alignment
        if spread >= self.spread_threshold and current_close > ema:
            conf = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_momentum_outperformance_lead",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema": ema,
                    "close": current_close
                }
            )

        # Strong ETH underperformance + local trend breakdown
        if spread <= -self.spread_threshold and current_close < ema:
            conf = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_underperformance_drag",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema": ema,
                    "close": current_close
                }
            )

        return None