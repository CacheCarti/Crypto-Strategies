from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
from collections import deque

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 72,
        "required_features": ["eth_return_pct", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_history = deque(maxlen=72)
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_zscore = 1.75
        self.exit_zscore = 0.35

    def _ema(self, values, period: int):
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
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        current_spread = eth_ret - btc_ret
        self.spread_history.append(current_spread)

        closes = ctx.closes(48)
        if len(closes) < 48 or len(self.spread_history) < 48:
            return None

        # Robust z-score of spread over trailing window
        history_list = list(self.spread_history)
        mean_spread = sum(history_list) / len(history_list)
        variance = sum((x - mean_spread) ** 2 for x in history_list) / len(history_list)
        spread_std = math.sqrt(variance)

        if spread_std < 1e-6:
            return None

        zscore = (current_spread - mean_spread) / spread_std

        # Position management: Mean-reversion exit
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and zscore <= self.exit_zscore:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_mean_reverted", "zscore": round(zscore, 2), "spread": round(current_spread, 2)}
                )
            elif direction == "short" and zscore >= -self.exit_zscore:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_mean_reverted", "zscore": round(zscore, 2), "spread": round(current_spread, 2)}
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Price-trend confirmation using EMA to ensure trend alignment
        ema_fast = self._ema(closes, 12)
        ema_slow = self._ema(closes, 36)
        if ema_fast is None or ema_slow is None:
            return None

        current_close = ctx.bar.close

        # Long: ETH relative outperformance breakout with price confirmation
        if zscore > self.min_zscore and current_spread > 1.2 and current_close > ema_fast > ema_slow:
            conf = min(0.9, 0.5 + 0.15 * min(zscore - self.min_zscore, 2.0))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_expansion",
                    "zscore": round(zscore, 2),
                    "spread": round(current_spread, 2),
                    "ema_fast": round(ema_fast, 2),
                    "close": round(current_close, 2)
                }
            )

        # Short: ETH relative underperformance breakdown with price confirmation
        if zscore < -self.min_zscore and current_spread < -1.2 and current_close < ema_fast < ema_slow:
            conf = min(0.9, 0.5 + 0.15 * min(abs(zscore) - self.min_zscore, 2.0))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_expansion",
                    "zscore": round(zscore, 2),
                    "spread": round(current_spread, 2),
                    "ema_fast": round(ema_fast, 2),
                    "close": round(current_close, 2)
                }
            )

        return None