from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RsiDivergenceStrategy(Strategy):
    METADATA = {
        "name": "RsiDivergenceEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _calc_rsi_series(self, closes, period=14):
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsi_series = []
        for i in range(period, len(closes)):
            sub_gains = gains[i - period:i]
            sub_losses = losses[i - period:i]
            avg_g = sum(sub_gains) / period
            avg_l = sum(sub_losses) / period
            if avg_l == 0:
                rsi_series.append(100.0)
            else:
                rs = avg_g / avg_l
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        current_close = closes[-1]

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion normalization exit
            if pos_dir == "long" and current_rsi >= 58.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_long", "rsi": current_rsi})
            elif pos_dir == "short" and current_rsi <= 42.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_short", "rsi": current_rsi})
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip during severe systemic crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Detect pivots in lookback window (excluding current bar)
        recent_closes = closes[-self.lookback:]
        recent_rsi = rsi_series[-self.lookback:]

        pivot_low_idx = -1
        pivot_low_price = float("inf")
        pivot_high_idx = -1
        pivot_high_price = float("-inf")

        # 3-bar local minimum/maximum detection
        for i in range(1, len(recent_closes) - 2):
            if recent_closes[i] <= recent_closes[i - 1] and recent_closes[i] <= recent_closes[i + 1]:
                if recent_closes[i] < pivot_low_price:
                    pivot_low_price = recent_closes[i]
                    pivot_low_idx = i
            if recent_closes[i] >= recent_closes[i - 1] and recent_closes[i] >= recent_closes[i + 1]:
                if recent_closes[i] > pivot_high_price:
                    pivot_high_price = recent_closes[i]
                    pivot_high_idx = i

        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Bullish Divergence: Price lower than prior pivot low, but RSI higher than prior pivot RSI
        if pivot_low_idx != -1 and (len(recent_closes) - 1 - pivot_low_idx) >= 3:
            prev_rsi = recent_rsi[pivot_low_idx]
            if current_close < pivot_low_price and current_rsi > (prev_rsi + 2.0) and current_rsi < 45.0:
                if funding_rate <= 0.0003:  # avoid crowded long positioning
                    conf = min(0.9, 0.6 + max(0.0, (current_rsi - prev_rsi) / 25.0))
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "prev_rsi": prev_rsi,
                            "close": current_close,
                            "prev_low": pivot_low_price,
                            "funding": funding_rate,
                        },
                    )

        # Bearish Divergence: Price higher than prior pivot high, but RSI lower than prior pivot RSI
        if pivot_high_idx != -1 and (len(recent_closes) - 1 - pivot_high_idx) >= 3:
            prev_rsi = recent_rsi[pivot_high_idx]
            if current_close > pivot_high_price and current_rsi < (prev_rsi - 2.0) and current_rsi > 55.0:
                if funding_rate >= -0.0003:  # avoid crowded short positioning
                    conf = min(0.9, 0.6 + max(0.0, (prev_rsi - current_rsi) / 25.0))
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "prev_rsi": prev_rsi,
                            "close": current_close,
                            "prev_high": pivot_high_price,
                            "funding": funding_rate,
                        },
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index