from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class PanicFlushRecovery(Strategy):
    METADATA = {
        "name": "Panic Flush Recovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.028  # 2.8% drop from peak to trough
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.target_midpoint = 0.0

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
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)
        opens = ctx.opens(self.lookback + 10)

        if len(closes) < self.lookback + 5:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        prev_high = highs[-2]
        prev_close = closes[-2]
        rsi = self._rsi(closes, 14)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # 1. Active Position Management
        if ctx.has_position():
            # Exit once price regains the flush midpoint
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "close": current_close,
                        "target_midpoint": round(self.target_midpoint, 2),
                        "rsi": round(rsi, 2) if rsi is not None else 50.0,
                    },
                )

            # Exit on overbought momentum exhaustion
            if rsi is not None and rsi >= 70.0:
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "rsi_overbought_exhaustion_exit",
                        "close": current_close,
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # 2. Check Cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 3. Analyze Recent Drop Window
        window_highs = highs[-self.lookback :]
        window_lows = lows[-self.lookback :]

        peak_price = max(window_highs)
        trough_price = min(window_lows)

        if peak_price <= 0 or trough_price <= 0:
            return None

        drop_magnitude = (peak_price - trough_price) / peak_price

        # Must meet the drop threshold
        is_flush = drop_magnitude >= self.drop_threshold

        # Stabilization confirmation:
        # Candle is green and either breaks above the prior bar's high or reclaims above prior close strongly
        is_green = current_close > current_open
        breaks_prev_high = current_close > prev_high
        strong_bounce = (current_close > prev_close) and ((current_close - trough_price) / trough_price >= 0.008)
        stabilized = is_green and (breaks_prev_high or strong_bounce)

        # RSI filter: avoid buying when already overextended (allow up to 55 to capture initial bounce momentum)
        rsi_valid = rsi is None or rsi <= 55.0

        if is_flush and stabilized and rsi_valid:
            midpoint = trough_price + (peak_price - trough_price) * 0.50

            # Ensure current price is still below the midpoint so there is upside room
            if current_close >= midpoint:
                return None

            expected_gain_bps = ((midpoint - current_close) / current_close) * 10000.0
            self.target_midpoint = midpoint

            tp_bps = min(max(expected_gain_bps, 180.0), 450.0)
            sl_bps = 280.0

            # Scale confidence with drop depth and fear level
            fear_boost = 0.10 if fg_index < 40.0 else 0.0
            confidence = min(0.60 + (drop_magnitude * 3.5) + fear_boost, 0.90)

            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=sl_bps,
                take_profit_bps=round(tp_bps, 1),
                metadata={
                    "reason": "panic_flush_stabilization_entry",
                    "drop_pct": round(drop_magnitude * 100.0, 2),
                    "flush_peak": round(peak_price, 2),
                    "flush_trough": round(trough_price, 2),
                    "target_midpoint": round(midpoint, 2),
                    "expected_gain_bps": round(expected_gain_bps, 1),
                    "rsi": round(rsi, 2) if rsi is not None else 50.0,
                    "fear_greed": fg_index,
                    "close": current_close,
                },
            )

        return None