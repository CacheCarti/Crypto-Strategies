from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRelativeStrengthRotation(Strategy):
    METADATA = {
        "name": "EthRelativeStrengthRotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0  # 2% spread
        self.rsi_period = 14
        self.ema_fast = 12
        self.ema_slow = 26
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 20

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def _rsi(self, closes, period: int = 14):
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature readings
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Calculate indicators
        rsi = self._rsi(closes, self.rsi_period)
        ema_f = self._ema(closes, self.ema_fast)
        ema_s = self._ema(closes, self.ema_slow)
        if rsi is None or ema_f is None or ema_s is None:
            return None

        current_price = ctx.bar.close

        # Position management: exit on mean reversion or exhaustion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Long exit: spread has mean-reverted back near neutral or RSI overbought
            if pos_dir == "long":
                if spread <= 0.25 or rsi >= 75.0 or bars_held >= self.max_hold_bars:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_exhaustion",
                            "spread": spread,
                            "rsi": rsi,
                            "bars_held": bars_held,
                            "price": current_price,
                        },
                    )

            # Short exit: spread has recovered or RSI oversold
            elif pos_dir == "short":
                if spread >= -0.25 or rsi <= 25.0 or bars_held >= self.max_hold_bars:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_exhaustion",
                            "spread": spread,
                            "rsi": rsi,
                            "bars_held": bars_held,
                            "price": current_price,
                        },
                    )
            return None

        # Check post-exit cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Avoid entry during extreme market crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: ETH strong outperformance + trend confirmation + not overbought
        if spread >= self.spread_threshold and ema_f > ema_s and 45.0 <= rsi <= 68.0:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.5 + (spread - self.spread_threshold) * 0.08, 0.9)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rel_strength_momentum_long",
                    "spread": spread,
                    "rsi": rsi,
                    "ema_fast": ema_f,
                    "ema_slow": ema_s,
                    "price": current_price,
                },
            )

        # Entry logic: ETH strong underperformance + trend confirmation + not oversold
        if spread <= -self.spread_threshold and ema_f < ema_s and 32.0 <= rsi <= 55.0:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.5 + (abs(spread) - self.spread_threshold) * 0.08, 0.9)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rel_weakness_momentum_short",
                    "spread": spread,
                    "rsi": rsi,
                    "ema_fast": ema_f,
                    "ema_slow": ema_s,
                    "price": current_price,
                },
            )

        return None