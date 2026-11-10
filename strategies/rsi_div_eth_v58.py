from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceEvent(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceEvent",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,  # 4 hours intended hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 32
        self.cooldown_bars = 12
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

    def _calculate_rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period + 2:
            return None

        # Build RSI series for the lookback window
        rsi_series = []
        for i in range(self.lookback + 1):
            sub_closes = closes[: len(closes) - self.lookback + i]
            r = self._calculate_rsi(sub_closes, self.rsi_period)
            rsi_series.append(r)

        current_rsi = rsi_series[-1]
        if current_rsi is None:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Time-based exhaustion exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_exhaustion", "bars_held": bars_held, "rsi": current_rsi}
                )

            # Normalization exits
            if pos_dir == "long" and current_rsi >= 55.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_normalized_bull", "rsi": current_rsi, "bars_held": bars_held}
                )
            elif pos_dir == "short" and current_rsi <= 45.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_normalized_bear", "rsi": current_rsi, "bars_held": bars_held}
                )
            return None

        # Hard Cooldown Guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Market filter: skip crisis or extreme meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        recent_closes = closes[-self.lookback:]
        
        # Identify pivot points in the window [0, lookback-3]
        # Current bar is lookback-1. Pivot must be confirmed by at least 2 bars.
        low_pivots = []
        high_pivots = []
        
        for idx in range(3, self.lookback - 2):
            # Pivot Low
            if (recent_closes[idx] < recent_closes[idx - 1] and
                recent_closes[idx] < recent_closes[idx - 2] and
                recent_closes[idx] < recent_closes[idx + 1] and
                recent_closes[idx] < recent_closes[idx + 2]):
                if rsi_series[idx] is not None:
                    low_pivots.append((idx, recent_closes[idx], rsi_series[idx]))

            # Pivot High
            if (recent_closes[idx] > recent_closes[idx - 1] and
                recent_closes[idx] > recent_closes[idx - 2] and
                recent_closes[idx] > recent_closes[idx + 1] and
                recent_closes[idx] > recent_closes[idx + 2]):
                if rsi_series[idx] is not None:
                    high_pivots.append((idx, recent_closes[idx], rsi_series[idx]))

        current_close = recent_closes[-1]

        # Bullish Divergence Event:
        # Price is printing a lower low than an established oversold pivot low,
        # but current RSI forms a distinct higher low while oversold/recovering (< 40).
        if current_rsi < 40.0 and len(low_pivots) > 0:
            # Check the most prominent previous low
            prev_idx, prev_low_price, prev_low_rsi = low_pivots[-1]
            if prev_low_rsi <= 35.0:
                price_drop = (prev_low_price - current_close) / prev_low_price
                if 0.003 <= price_drop <= 0.04 and current_rsi > (prev_low_rsi + 2.5):
                    self.entry_bar = ctx.bar_index
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=250.0,
                        take_profit_bps=450.0,
                        horizon_seconds=14400,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "prev_rsi": round(prev_low_rsi, 2),
                            "price": round(current_close, 2),
                            "prev_low_price": round(prev_low_price, 2),
                        }
                    )

        # Bearish Divergence Event:
        # Price is printing a higher high than an established overbought pivot high,
        # but current RSI forms a lower high while overbought/waning (> 60).
        if current_rsi > 60.0 and len(high_pivots) > 0:
            prev_idx, prev_high_price, prev_high_rsi = high_pivots[-1]
            if prev_high_rsi >= 65.0:
                price_rise = (current_close - prev_high_price) / prev_high_price
                if 0.003 <= price_rise <= 0.04 and current_rsi < (prev_high_rsi - 2.5):
                    self.entry_bar = ctx.bar_index
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=250.0,
                        take_profit_bps=450.0,
                        horizon_seconds=14400,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "prev_rsi": round(prev_high_rsi, 2),
                            "price": round(current_close, 2),
                            "prev_high_price": round(prev_high_price, 2),
                        }
                    )

        return None