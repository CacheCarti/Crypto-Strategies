from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceAlpha(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceAlpha",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,  # ~4 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 32
        self.pivot_window = 3
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        if len(gains) < period:
            return []
            
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rsi_series = []
        if avg_loss == 0.0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
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

    def _find_pivots(self, series: list, window: int = 3):
        low_pivots = []
        high_pivots = []
        n = len(series)
        for i in range(window, n - window):
            val = series[i]
            is_low = True
            is_high = True
            for offset in range(1, window + 1):
                if series[i - offset] <= val or series[i + offset] <= val:
                    is_low = False
                if series[i - offset] >= val or series[i + offset] >= val:
                    is_high = False
            if is_low:
                low_pivots.append((i, val))
            if is_high:
                high_pivots.append((i, val))
        return low_pivots, high_pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        # Build RSI series aligned with closes
        rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback:
            return None

        current_rsi = rsi_vals[-1]
        bars_held = ctx.bar_index - self.last_entry_bar if ctx.has_position() else 0

        # Position exit logic: RSI normalization or time decay
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (current_rsi >= 58.0 or bars_held >= 12):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_rsi_normalized_or_timeout",
                    "rsi": round(current_rsi, 2),
                    "bars_held": bars_held
                })
            elif pos_dir == "short" and (current_rsi <= 42.0 or bars_held >= 12):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_rsi_normalized_or_timeout",
                    "rsi": round(current_rsi, 2),
                    "bars_held": bars_held
                })
            return None

        # Cooldown guard after recent exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Cross-asset & volatility confirmation (addressing prior failures of pure divergence)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None  # Avoid false divergence traps in panics

        btc_ret = ctx.market.get("btc_return_pct", 0.0)

        # Look at last `lookback` points for pivots
        sub_closes = closes[-self.lookback:]
        sub_rsi = rsi_vals[-self.lookback:]

        low_pivots_c, high_pivots_c = self._find_pivots(sub_closes, self.pivot_window)
        
        # Bullish Divergence Check:
        # Need at least two price low pivots where second low is lower,
        # but RSI at second low is strictly higher than RSI at first low.
        if len(low_pivots_c) >= 2:
            p1_idx, p1_price = low_pivots_c[-2]
            p2_idx, p2_price = low_pivots_c[-1]
            
            # Pivot must be relatively recent (within last 6 bars)
            if (self.lookback - 1 - p2_idx) <= 5:
                r1 = sub_rsi[p1_idx]
                r2 = sub_rsi[p2_idx]
                
                if p2_price < p1_price and r2 > r1 and r2 < 42.0 and btc_ret > -3.0:
                    # Current price starting to tick up from recent pivot
                    if sub_closes[-1] > sub_closes[-2]:
                        self.last_entry_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "bullish_rsi_div_confirmed",
                                "p1_price": p1_price,
                                "p2_price": p2_price,
                                "r1": round(r1, 2),
                                "r2": round(r2, 2),
                                "rsi": round(current_rsi, 2),
                                "btc_ret": round(btc_ret, 2)
                            }
                        )

        # Bearish Divergence Check:
        # Need at least two price high pivots where second high is higher,
        # but RSI at second high is strictly lower than RSI at first high.
        if len(high_pivots_c) >= 2:
            p1_idx, p1_price = high_pivots_c[-2]
            p2_idx, p2_price = high_pivots_c[-1]
            
            if (self.lookback - 1 - p2_idx) <= 5:
                r1 = sub_rsi[p1_idx]
                r2 = sub_rsi[p2_idx]
                
                if p2_price > p1_price and r2 < r1 and r2 > 58.0 and btc_ret < 3.0:
                    if sub_closes[-1] < sub_closes[-2]:
                        self.last_entry_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "bearish_rsi_div_confirmed",
                                "p1_price": p1_price,
                                "p2_price": p2_price,
                                "r1": round(r1, 2),
                                "r2": round(r2, 2),
                                "rsi": round(current_rsi, 2),
                                "btc_ret": round(btc_ret, 2)
                            }
                        )

        return None