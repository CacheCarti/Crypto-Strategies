from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingCarry(Strategy):
    METADATA = {
        "name": "BtcFundingCarry",
        "domain": "btc_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 18
        self.slow_period = 48
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.eval_step = 3
        self.last_signal_bar = -100
        self.neg_funding_thresh = -0.00005  # -0.005% per 8h
        self.pos_funding_thresh = 0.00025   # +0.025% per 8h

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
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 2:
            return None

        current_price = ctx.bar.close
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Position management and regime exits
        if has_pos:
            if pos_dir == "long":
                # Exit long if funding gets excessively overheated or trend reverses down
                if funding_rate > 0.00045 or (current_price < fast_ema and rsi > 70.0):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "exit_long_overheated_funding_or_rsi",
                            "funding_rate": funding_rate,
                            "rsi": rsi,
                            "fast_ema": fast_ema,
                            "price": current_price,
                        }
                    )
            elif pos_dir == "short":
                # Exit short if funding becomes negative (shorts begin paying) or oversold rebound
                if funding_rate < self.neg_funding_thresh or (current_price > fast_ema and rsi < 30.0):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "exit_short_negative_funding_or_oversold",
                            "funding_rate": funding_rate,
                            "rsi": rsi,
                            "fast_ema": fast_ema,
                            "price": current_price,
                        }
                    )

        # Gated entry evaluation to avoid overtrading
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        if ctx.bar_index % self.eval_step != 0:
            return None

        # Long Setup: Negative/low funding (shorts pay longs) + price above slow EMA + non-overbought RSI
        if funding_rate <= self.neg_funding_thresh and current_price >= slow_ema and rsi < 65.0:
            if not (has_pos and pos_dir == "long"):
                self.last_signal_bar = ctx.bar_index
                conf = min(0.9, 0.65 + abs(funding_rate) * 500.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "negative_funding_carry_bull_alignment",
                        "funding_rate": funding_rate,
                        "rsi": rsi,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "price": current_price,
                    }
                )

        # Short Setup: Persistently high positive funding (longs paying shorts) + price below fast EMA + RSI < 55
        if funding_rate >= self.pos_funding_thresh and current_price < fast_ema and rsi < 55.0:
            if not (has_pos and pos_dir == "short"):
                self.last_signal_bar = ctx.bar_index
                conf = min(0.85, 0.60 + funding_rate * 400.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "positive_funding_overcrowded_bear_alignment",
                        "funding_rate": funding_rate,
                        "rsi": rsi,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "price": current_price,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_signal_bar = ctx.bar_index