from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class FundingRateContrarianSqueeze(Strategy):
    METADATA = {
        "name": "FundingRateContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_window = deque(maxlen=6)
        self.last_exit_bar = -100
        self.cooldown_bars = 5
        self.pos_direction = None
        
        # Funding rate thresholds (8h rate decimals)
        self.neg_funding_thresh = -0.00003  # Crowd heavily short / paying
        self.pos_funding_thresh = 0.00018   # Crowd heavily long / over-leveraged
        self.neutral_band_low = 0.00002
        self.neutral_band_high = 0.00008

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
        self.pos_direction = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track funding rate history
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_window.append(current_funding)

        if len(self.funding_window) < 3:
            return None

        # 3-bar smoothed funding rate
        avg_funding = sum(list(self.funding_window)[-3:]) / 3.0
        rsi = self._rsi(closes, period=14)
        ema_trend = self._ema(closes, period=30)
        
        if rsi is None or ema_trend is None:
            return None

        price = ctx.bar.close
        has_pos = ctx.has_position()

        # Exit logic when in position
        if has_pos:
            pos_dir = ctx.position_direction()
            
            # Long Exit: funding normalized or RSI overbought
            if pos_dir == "long":
                if avg_funding >= self.neutral_band_low or rsi >= 68.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_funding_normalized_or_overbought",
                            "avg_funding": avg_funding,
                            "rsi": rsi,
                            "price": price,
                        }
                    )
            
            # Short Exit: funding normalized or RSI oversold
            elif pos_dir == "short":
                if avg_funding <= self.neutral_band_high or rsi <= 32.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_funding_normalized_or_oversold",
                            "avg_funding": avg_funding,
                            "rsi": rsi,
                            "price": price,
                        }
                    )
            return None

        # Entry guard: mandatory cooldown after prior trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Setup: Persistent negative funding (crowd paying to short) + not in a catastrophic freefall
        if avg_funding <= self.neg_funding_thresh and current_funding <= self.neg_funding_thresh:
            if 30.0 <= rsi <= 58.0 and price >= (ema_trend * 0.975):
                conf = min(0.9, 0.65 + abs(avg_funding) * 1000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_negative_squeeze_long",
                        "avg_funding": avg_funding,
                        "current_funding": current_funding,
                        "rsi": rsi,
                        "ema_trend": ema_trend,
                        "price": price,
                    }
                )

        # Short Setup: Persistent positive funding (crowd excessively leveraged long) + not in parabolic runaway
        if avg_funding >= self.pos_funding_thresh and current_funding >= self.pos_funding_thresh:
            if 42.0 <= rsi <= 70.0 and price <= (ema_trend * 1.025):
                conf = min(0.9, 0.65 + avg_funding * 800.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_positive_exhaustion_short",
                        "avg_funding": avg_funding,
                        "current_funding": current_funding,
                        "rsi": rsi,
                        "ema_trend": ema_trend,
                        "price": price,
                    }
                )

        return None