from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,  # 4-hour target hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 35
        self.pivot_window = 2
        self.cooldown_bars = 6
        self.last_trade_bar = -100

    def _calculate_rsi_series(self, closes):
        if len(closes) < self.rsi_period + 1:
            return []
        
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
            
        rsi_series = []
        avg_gain = sum(gains[:self.rsi_period]) / self.rsi_period
        avg_loss = sum(losses[:self.rsi_period]) / self.rsi_period
        
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        
        for i in range(self.rsi_period, len(gains)):
            avg_gain = (avg_gain * (self.rsi_period - 1) + gains[i]) / self.rsi_period
            avg_loss = (avg_loss * (self.rsi_period - 1) + losses[i]) / self.rsi_period
            if avg_loss == 0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)

        if len(closes) < self.lookback + self.rsi_period + 2:
            return None

        rsi_series = self._calculate_rsi_series(closes)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        bars_since_trade = ctx.bar_index - self.last_trade_bar

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean-reversion normalization exit
            if pos_dir == "long" and current_rsi >= 58.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_long", "rsi": round(current_rsi, 2)})
            if pos_dir == "short" and current_rsi <= 42.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_short", "rsi": round(current_rsi, 2)})
            return None

        if bars_since_trade < self.cooldown_bars:
            return None

        # Filter extreme crisis environments
        if ctx.market.get("regime", "NORMAL") in ("CRISIS", "MELTDOWN"):
            return None

        # Detect swing lows and swing highs over recent window
        w = self.pivot_window
        n = len(rsi_series)
        
        # Look for the most recent completed pivot (at index -2 or -3) and an older pivot
        recent_low_pivots = []
        recent_high_pivots = []
        
        # Check pivot points in the window [n - lookback, n - w - 1]
        start_idx = max(w, n - self.lookback)
        end_idx = n - w
        
        for i in range(start_idx, end_idx):
            # Pivot Low
            if all(lows[i] <= lows[i - k] for k in range(1, w + 1)) and all(lows[i] <= lows[i + k] for k in range(1, w + 1)):
                recent_low_pivots.append((i, lows[i], rsi_series[i]))
            # Pivot High
            if all(highs[i] >= highs[i - k] for k in range(1, w + 1)) and all(highs[i] >= highs[i + k] for k in range(1, w + 1)):
                recent_high_pivots.append((i, highs[i], rsi_series[i]))

        # Bullish Divergence: Lower price low, higher RSI low, with RSI oversold context
        if len(recent_low_pivots) >= 2:
            p_prev, p_curr = recent_low_pivots[-2], recent_low_pivots[-1]
            idx_diff = p_curr[0] - p_prev[0]
            if 5 <= idx_diff <= 25 and (n - 1 - p_curr[0]) <= 3:
                # Lower low in price, higher low in RSI
                if p_curr[1] < p_prev[1] and p_curr[2] > p_prev[2] and p_curr[2] < 45.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.8,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "p_curr_rsi": round(p_curr[2], 2),
                            "p_prev_rsi": round(p_prev[2], 2),
                            "p_curr_low": round(p_curr[1], 2),
                            "p_prev_low": round(p_prev[1], 2),
                        }
                    )

        # Bearish Divergence: Higher price high, lower RSI high, with RSI overbought context
        if len(recent_high_pivots) >= 2:
            p_prev, p_curr = recent_high_pivots[-2], recent_high_pivots[-1]
            idx_diff = p_curr[0] - p_prev[0]
            if 5 <= idx_diff <= 25 and (n - 1 - p_curr[0]) <= 3:
                # Higher high in price, lower high in RSI
                if p_curr[1] > p_prev[1] and p_curr[2] < p_prev[2] and p_curr[2] > 55.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.8,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "p_curr_rsi": round(p_curr[2], 2),
                            "p_prev_rsi": round(p_prev[2], 2),
                            "p_curr_high": round(p_curr[1], 2),
                            "p_prev_high": round(p_prev[1], 2),
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index