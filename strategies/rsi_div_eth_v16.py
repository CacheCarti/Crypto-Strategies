from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 3
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.entry_bar = 0

    def _calc_rsi_series(self, closes, period):
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = [0.0] * n
        losses = [0.0] * n
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains[i] = diff
            else:
                losses[i] = -diff

        avg_gain = sum(gains[1:period + 1]) / period
        avg_loss = sum(losses[1:period + 1]) / period

        rsi_vals = [None] * period
        first_rsi = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
        rsi_vals.append(first_rsi)

        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_vals.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_vals

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.pivot_window + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.pivot_window + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.pivot_window + self.rsi_period + 5)

        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        current_close = closes[-1]

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Normalize exits or time-stop
            if direction == "long" and (current_rsi > 58.0 or bars_held >= 18):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_long", "rsi": current_rsi, "bars_held": bars_held})
            elif direction == "short" and (current_rsi < 42.0 or bars_held >= 18):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_short", "rsi": current_rsi, "bars_held": bars_held})
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: avoid severe market stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        pw = self.pivot_window
        eval_range = range(len(closes) - self.lookback, len(closes) - pw)

        # Detect Pivot Lows for Bullish Divergence
        pivot_lows = []
        for i in eval_range:
            if i < pw or i >= len(lows) - pw:
                continue
            is_pivot = True
            for k in range(1, pw + 1):
                if lows[i] >= lows[i - k] or lows[i] >= lows[i + k]:
                    is_pivot = False
                    break
            if is_pivot and rsi_series[i] is not None:
                pivot_lows.append((i, lows[i], rsi_series[i]))

        if len(pivot_lows) >= 2:
            prev_low = pivot_lows[-2]
            last_low = pivot_lows[-1]
            # Ensure the most recent pivot was formed recently
            if (len(closes) - 1 - last_low[0]) <= 5:
                # Bullish divergence: price lower low, RSI higher low, RSI not overbought
                if last_low[1] < prev_low[1] and last_low[2] > prev_low[2] + 1.5 and last_low[2] < 42.0:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "price": current_close,
                            "pivot_rsi_delta": round(last_low[2] - prev_low[2], 2),
                        }
                    )

        # Detect Pivot Highs for Bearish Divergence
        pivot_highs = []
        for i in eval_range:
            if i < pw or i >= len(highs) - pw:
                continue
            is_pivot = True
            for k in range(1, pw + 1):
                if highs[i] <= highs[i - k] or highs[i] <= highs[i + k]:
                    is_pivot = False
                    break
            if is_pivot and rsi_series[i] is not None:
                pivot_highs.append((i, highs[i], rsi_series[i]))

        if len(pivot_highs) >= 2:
            prev_high = pivot_highs[-2]
            last_high = pivot_highs[-1]
            if (len(closes) - 1 - last_high[0]) <= 5:
                # Bearish divergence: price higher high, RSI lower high, RSI not oversold
                if last_high[1] > prev_high[1] and last_high[2] < prev_high[2] - 1.5 and last_high[2] > 58.0:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "price": current_close,
                            "pivot_rsi_delta": round(prev_high[2] - last_high[2], 2),
                        }
                    )

        return None