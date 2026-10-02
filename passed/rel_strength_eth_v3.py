from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RelativeStrengthRotation(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Strength Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 1.1
        self.exit_spread_threshold = 0.2
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.ema_period = 20
        self.rsi_period = 14

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        current_price = ctx.bar.close

        if ema is None or rsi is None:
            return None

        # Position exit management on mean reversion of spread
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and spread < self.exit_spread_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "eth_btc_spread_mean_reverted_long_exit",
                        "spread": spread,
                        "eth_ret": eth_ret,
                        "btc_ret": btc_ret,
                        "rsi": rsi,
                        "price": current_price
                    }
                )
            elif direction == "short" and spread > -self.exit_spread_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "eth_btc_spread_mean_reverted_short_exit",
                        "spread": spread,
                        "eth_ret": eth_ret,
                        "btc_ret": btc_ret,
                        "rsi": rsi,
                        "price": current_price
                    }
                )
            return None

        # Post-exit cooldown gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis conditions
        if ctx.market.get("regime", "NORMAL") == "MELTDOWN":
            return None

        # Long Entry: ETH outperforming BTC with non-overbought momentum
        if spread > self.spread_threshold and rsi < 75.0:
            confidence = min(0.9, 0.55 + max(0.0, spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_momentum_entry",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price
                }
            )

        # Short Entry: ETH lagging BTC with non-oversold momentum
        if spread < -self.spread_threshold and rsi > 25.0:
            confidence = min(0.9, 0.55 + max(0.0, abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_momentum_entry",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price
                }
            )

        return None