from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.funding_history = []
        self.high_funding_thresh = 0.00035
        self.low_funding_thresh = -0.00025
        self.norm_high = 0.00010
        self.norm_low = -0.00010

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1.0 - k)
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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup)
        if len(closes) < self.warmup:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Handle active position exit conditions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if pos_dir == "long" and current_funding >= self.norm_low:
                should_exit = True
                exit_reason = "funding_normalized_long"
            elif pos_dir == "short" and current_funding <= self.norm_high:
                should_exit = True
                exit_reason = "funding_normalized_short"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": exit_reason, "funding": current_funding, "close": ctx.bar.close}
                )
            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if len(self.funding_history) < 3:
            return None

        # Check funding persistence over the last 3 bars
        f_recent = self.funding_history[-3:]
        is_persistently_negative = all(f <= self.low_funding_thresh for f in f_recent)
        is_persistently_positive = all(f >= self.high_funding_thresh for f in f_recent)

        if not (is_persistently_negative or is_persistently_positive):
            return None

        # Price & Regime confirmations
        # 1. Volatility state: avoid extreme crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 2. Moving average and RSI to avoid fading runaway structural trends
        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or rsi14 is None:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        close = ctx.bar.close

        # Long Squeeze: crowded shorts paying, price not in free-fall (RSI > 32 and not collapsed far below EMA)
        if is_persistently_negative:
            distance_from_ema = (close - ema20) / ema20
            if distance_from_ema > -0.04 and rsi14 > 32.0 and btc_ret > -3.5:
                confidence = min(0.9, 0.65 + abs(current_funding) * 500)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_negative_squeeze_entry",
                        "funding": current_funding,
                        "rsi": round(rsi14, 2),
                        "ema20": round(ema20, 2),
                        "btc_ret": round(btc_ret, 2)
                    }
                )

        # Short Squeeze/Fade: crowded longs paying, price not runaway parabolic
        if is_persistently_positive:
            distance_from_ema = (close - ema20) / ema20
            if distance_from_ema < 0.04 and rsi14 < 68.0 and btc_ret < 3.5:
                confidence = min(0.9, 0.65 + abs(current_funding) * 500)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_positive_exhaustion_entry",
                        "funding": current_funding,
                        "rsi": round(rsi14, 2),
                        "ema20": round(ema20, 2),
                        "btc_ret": round(btc_ret, 2)
                    }
                )

        return None