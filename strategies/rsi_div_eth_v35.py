from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceAlpha(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceAlpha",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 32
        self.cooldown_bars = 6
        self.last_trade_bar = -100
        self.entry_bar = -100

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def _get_rsi_series(self, closes: list, length: int) -> list:
        series = []
        for i in range(len(closes) - length, len(closes)):
            sub = closes[: i + 1]
            val = self._calc_rsi(sub, self.rsi_period)
            series.append(val if val is not None else 50.0)
        return series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Calculate current RSI
        current_rsi = self._calc_rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        # Position management: exit on normalization or timeout
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            direction = ctx.position_direction()

            # Exit if RSI has mean-reverted or time stop reached
            if direction == "long" and (current_rsi >= 58.0 or bars_held >= 14):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_long", "rsi": current_rsi, "bars_held": bars_held})
            elif direction == "short" and (current_rsi <= 42.0 or bars_held >= 14):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_short", "rsi": current_rsi, "bars_held": bars_held})
            return None

        # Cooldown guard to prevent overtrading and fee drain
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Compute RSI series for swing analysis over lookback window
        rsi_series = self._get_rsi_series(closes, self.lookback)
        price_series = closes[-self.lookback:]

        # Find prominent swing pivots (window: 5 to lookback-3)
        # Bullish divergence setup:
        # Current bar is in an oversold recovery zone
        # Earlier pivot was deeply oversold, current price is lower but current RSI is higher
        if current_rsi < 38.0:
            pivot_idx = -1
            min_p = float("inf")
            for i in range(3, self.lookback - 4):
                if price_series[i] < price_series[i - 1] and price_series[i] < price_series[i + 1]:
                    if rsi_series[i] < 34.0 and price_series[i] < min_p:
                        min_p = price_series[i]
                        pivot_idx = i

            if pivot_idx != -1:
                prev_price = price_series[pivot_idx]
                prev_rsi = rsi_series[pivot_idx]
                curr_price = price_series[-1]

                # Price made lower low, but RSI formed higher low (Bullish Divergence)
                if curr_price < prev_price and current_rsi > prev_rsi + 3.0:
                    self.last_trade_bar = ctx.bar_index
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "prev_rsi": round(prev_rsi, 2),
                            "curr_price": round(curr_price, 2),
                            "prev_price": round(prev_price, 2),
                        },
                    )

        # Bearish divergence setup:
        # Current bar is in an overbought pullback zone
        # Earlier pivot was deeply overbought, current price is higher but current RSI is lower
        if current_rsi > 62.0:
            pivot_idx = -1
            max_p = float("-inf")
            for i in range(3, self.lookback - 4):
                if price_series[i] > price_series[i - 1] and price_series[i] > price_series[i + 1]:
                    if rsi_series[i] > 66.0 and price_series[i] > max_p:
                        max_p = price_series[i]
                        pivot_idx = i

            if pivot_idx != -1:
                prev_price = price_series[pivot_idx]
                prev_rsi = rsi_series[pivot_idx]
                curr_price = price_series[-1]

                # Price made higher high, but RSI formed lower high (Bearish Divergence)
                if curr_price > prev_price and current_rsi < prev_rsi - 3.0:
                    self.last_trade_bar = ctx.bar_index
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "prev_rsi": round(prev_rsi, 2),
                            "curr_price": round(curr_price, 2),
                            "prev_price": round(prev_price, 2),
                        },
                    )

        return None