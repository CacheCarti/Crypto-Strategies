from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingCarryTrend(Strategy):
    METADATA = {
        "name": "BTC Funding Carry & Trend Strategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 60,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 18
        self.slow_period = 48
        self.rsi_period = 14
        self.funding_window = 8
        self.cooldown_bars = 6
        self.last_trade_bar = -100
        self.last_exit_bar = -100
        self.funding_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 10)
        if len(closes) < self.slow_period + 5:
            return None

        # Track and smooth funding rate
        raw_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(raw_funding)
        if len(self.funding_history) > self.funding_window:
            self.funding_history.pop(0)
        smooth_funding = sum(self.funding_history) / len(self.funding_history)

        ema_fast = self._ema(closes, self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()
        bars_since_trade = ctx.bar_index - self.last_trade_bar
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Position management and exit evaluation
        if has_pos:
            if pos_dir == "long":
                # Exit long if funding gets excessively positive or price loses slow trend
                if smooth_funding > 0.00035 or price < ema_slow * 0.992:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_carry_decay_or_trend_loss",
                            "funding": smooth_funding,
                            "price": price,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                        },
                    )
            elif pos_dir == "short":
                # Exit short if funding flips negative or price reclaims slow trend
                if smooth_funding < -0.0001 or price > ema_slow * 1.008:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_carry_exhaustion_or_reversal",
                            "funding": smooth_funding,
                            "price": price,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                        },
                    )

        # Enforce entry cooldown after last trade / exit
        if bars_since_trade < self.cooldown_bars or bars_since_exit < self.cooldown_bars:
            return None

        # Re-evaluate entries at low-frequency check (cadence)
        if ctx.bar_index % 2 != 0 and not has_pos:
            return None

        # Long Setup: Negative or subdued funding carry with bullish trend alignment
        long_carry_ok = smooth_funding <= 0.00008
        bull_trend = (price > ema_fast) and (ema_fast >= ema_slow * 0.998)
        rsi_bull_window = 38.0 <= rsi <= 68.0

        if long_carry_ok and bull_trend and rsi_bull_window and pos_dir != "long":
            self.last_trade_bar = ctx.bar_index
            # Boost confidence when funding is strictly negative (shorts pay longs)
            confidence = 0.78 if smooth_funding < 0.0 else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "negative_or_neutral_funding_bull_trend",
                    "funding": smooth_funding,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "rsi": rsi,
                    "price": price,
                },
            )

        # Short Setup: Overheated long funding carry with bearish trend breakdown
        short_carry_ok = smooth_funding >= 0.00022
        bear_trend = (price < ema_fast) and (ema_fast <= ema_slow * 1.002)
        rsi_bear_window = 32.0 <= rsi <= 62.0

        if short_carry_ok and bear_trend and rsi_bear_window and pos_dir != "short":
            self.last_trade_bar = ctx.bar_index
            confidence = 0.75 if smooth_funding > 0.00035 else 0.65
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "overheated_funding_bear_breakdown",
                    "funding": smooth_funding,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "rsi": rsi,
                    "price": price,
                },
            )

        return None