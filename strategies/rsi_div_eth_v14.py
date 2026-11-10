from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceVolConfirmed(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceVolConfirmed",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_series = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def _find_pivot_low(self, arr: list, left: int = 3, right: int = 3) -> list:
        pivots = []
        for i in range(left, len(arr) - right):
            val = arr[i]
            is_low = True
            for j in range(i - left, i + right + 1):
                if j != i and arr[j] <= val:
                    is_low = False
                    break
            if is_low:
                pivots.append(i)
        return pivots

    def _find_pivot_high(self, arr: list, left: int = 3, right: int = 3) -> list:
        pivots = []
        for i in range(left, len(arr) - right):
            val = arr[i]
            is_high = True
            for j in range(i - left, i + right + 1):
                if j != i and arr[j] >= val:
                    is_high = False
                    break
            if is_high:
                pivots.append(i)
        return pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n_bars = 45
        closes = ctx.closes(n_bars)
        if len(closes) < n_bars:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < 30:
            return None

        current_rsi = rsi_vals[-1]

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            # Exit long on RSI normalization/overbought or max hold
            if direction == "long":
                if current_rsi >= 58.0 or bars_held >= 10:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_rsi_normalized_or_time", "rsi": current_rsi, "bars_held": bars_held})
            
            # Exit short on RSI normalization/oversold or max hold
            elif direction == "short":
                if current_rsi <= 42.0 or bars_held >= 10:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_rsi_normalized_or_time", "rsi": current_rsi, "bars_held": bars_held})
            return None

        # Cross-asset trend and volatility state filters
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Pivot search on closes and RSI over the last 30 bars
        window_closes = closes[-self.lookback:]
        window_rsi = rsi_vals[-self.lookback:]

        low_pivots = self._find_pivot_low(window_closes, left=3, right=2)
        high_pivots = self._find_pivot_high(window_closes, left=3, right=2)

        # Bullish divergence check:
        # 1. Two distinct swing lows within lookback
        # 2. Price made a lower low, but RSI made a higher low
        # 3. Recent pivot must be recent (within last 8 bars)
        # 4. Filtered by BTC return not in steep multi-day drawdown (btc_ret > -3.0)
        if len(low_pivots) >= 2:
            p1, p2 = low_pivots[-2], low_pivots[-1]
            if (self.lookback - 1) - p2 <= 6:
                price_p1, price_p2 = window_closes[p1], window_closes[p2]
                rsi_p1, rsi_p2 = window_rsi[p1], window_rsi[p2]

                if price_p2 < price_p1 and rsi_p2 > rsi_p1 and rsi_p1 < 38.0 and current_rsi < 50.0:
                    if btc_ret > -3.5:
                        self.entry_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.80,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bullish_rsi_div_vol_confirmed",
                                "rsi": current_rsi,
                                "p1_rsi": rsi_p1,
                                "p2_rsi": rsi_p2,
                                "p1_price": price_p1,
                                "p2_price": price_p2,
                                "btc_ret": btc_ret,
                            },
                        )

        # Bearish divergence check:
        # 1. Two distinct swing highs within lookback
        # 2. Price made a higher high, but RSI made a lower high
        # 3. Recent pivot must be recent (within last 8 bars)
        # 4. Filtered by BTC return not in parabolic blow-off (btc_ret < 4.0)
        if len(high_pivots) >= 2:
            p1, p2 = high_pivots[-2], high_pivots[-1]
            if (self.lookback - 1) - p2 <= 6:
                price_p1, price_p2 = window_closes[p1], window_closes[p2]
                rsi_p1, rsi_p2 = window_rsi[p1], window_rsi[p2]

                if price_p2 > price_p1 and rsi_p2 < rsi_p1 and rsi_p1 > 62.0 and current_rsi > 50.0:
                    if btc_ret < 4.0:
                        self.entry_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.80,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bearish_rsi_div_vol_confirmed",
                                "rsi": current_rsi,
                                "p1_rsi": rsi_p1,
                                "p2_rsi": rsi_p2,
                                "p1_price": price_p1,
                                "p2_price": price_p2,
                                "btc_ret": btc_ret,
                            },
                        )

        return None