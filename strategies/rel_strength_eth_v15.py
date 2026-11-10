from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,  # ~4 hours hold for 1h bars
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 5
        self.last_exit_bar = -100
        self.spread_threshold = 2.0  # 2.0% return difference
        self.ema_fast_len = 12
        self.ema_slow_len = 26
        self.min_bars_held = 3

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

        # Price confirmation indicators
        ema_fast = self._ema(closes, self.ema_fast_len)
        ema_slow = self._ema(closes, self.ema_slow_len)
        if ema_fast is None or ema_slow is None:
            return None

        current_close = ctx.bar.close
        trend_up = ema_fast > ema_slow and current_close > ema_fast
        trend_down = ema_fast < ema_slow and current_close < ema_fast

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean-reversion exit: when ETH-BTC spread reverses or shrinks near zero
            if direction == "long" and spread < 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reversion_flat", "spread": round(spread, 3), "close": current_close}
                )
            elif direction == "short" and spread > -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reversion_flat", "spread": round(spread, 3), "close": current_close}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: ETH strong outperformance with upward price alignment
        if spread >= self.spread_threshold and trend_up:
            confidence = min(1.0, 0.65 + min(abs(spread) - self.spread_threshold, 3.0) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_trend_confirm",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2)
                }
            )

        # Entry logic: ETH strong underperformance with downward price alignment
        if spread <= -self.spread_threshold and trend_down:
            confidence = min(1.0, 0.65 + min(abs(spread) - self.spread_threshold, 3.0) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_trend_confirm",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2)
                }
            )

        return None