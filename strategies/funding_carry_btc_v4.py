from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingCarrySwing(Strategy):
    METADATA = {
        "name": "BTC Funding Carry Swing",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 60,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 21
        self.slow_period = 55
        self.rsi_period = 14
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.cooldown_bars = 16
        self.funding_long_thresh = 0.00003
        self.funding_short_thresh = 0.00022

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.slow_period + 20)
        if len(closes) < self.slow_period + 10:
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        price = ctx.bar.close
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit management for open positions
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            if pos_dir == "long":
                if funding > self.funding_short_thresh or (rsi > 78.0 and price < ema_fast):
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_carry_invalidation_or_overbought",
                            "funding_rate": funding,
                            "rsi": rsi,
                            "price": price,
                            "bars_held": bars_held
                        }
                    )
            elif pos_dir == "short":
                if funding < self.funding_long_thresh or (rsi < 22.0 and price > ema_fast):
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_carry_invalidation_or_oversold",
                            "funding_rate": funding,
                            "rsi": rsi,
                            "price": price,
                            "bars_held": bars_held
                        }
                    )
            return None

        # Entry logic (when flat)
        # Long Carry Setup: Discounted/neutral funding + upward momentum above EMA
        if funding <= self.funding_long_thresh and ema_fast > ema_slow:
            if price > ema_fast and 45.0 <= rsi <= 65.0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "favorable_carry_long_momentum",
                        "funding_rate": funding,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": price
                    }
                )

        # Short Carry Setup: Crowded positive funding + downward trend confirmation
        if funding >= self.funding_short_thresh and ema_fast < ema_slow:
            if price < ema_fast and 35.0 <= rsi <= 55.0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.70,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "overcrowded_long_funding_short_trend",
                        "funding_rate": funding,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": price
                    }
                )

        return None