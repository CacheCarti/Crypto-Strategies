from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # 6 hours target hold on 1h bars
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.0  # 2.0% return spread threshold
        self.rsi_period = 14
        self.ema_period = 24

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature extraction
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Calculate confirmation indicators
        rsi_val = self._rsi(closes, self.rsi_period)
        ema_val = self._ema(closes, self.ema_period)
        if rsi_val is None or ema_val is None:
            return None

        price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic: mean-reversion of the spread towards neutral
        if has_pos:
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_mean_reverted_long_exit",
                    "spread": spread,
                    "rsi": rsi_val,
                    "price": price
                })
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_mean_reverted_short_exit",
                    "spread": spread,
                    "rsi": rsi_val,
                    "price": price
                })
            return None

        # Cooldown guard: must wait N bars after previous trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long Entry: ETH outperforming BTC by > threshold, supported by price above EMA and not overbought
        if spread > self.spread_threshold and price > ema_val and rsi_val < 68.0:
            conf = min(0.9, 0.6 + max(0.0, (spread - self.spread_threshold) * 0.05))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_momentum_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi_val,
                    "ema": ema_val,
                    "price": price
                }
            )

        # Short Entry: ETH underperforming BTC by > threshold, supported by price below EMA and not oversold
        if spread < -self.spread_threshold and price < ema_val and rsi_val > 32.0:
            conf = min(0.9, 0.6 + max(0.0, (-spread - self.spread_threshold) * 0.05))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_momentum_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi_val,
                    "ema": ema_val,
                    "price": price
                }
            )

        return None