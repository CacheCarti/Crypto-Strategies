from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 10
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 12

    def _rsi_series(self, closes, period=14):
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = [0.0] * (n - 1)
        losses = [0.0] * (n - 1)
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains[i - 1] = diff
            else:
                losses[i - 1] = -diff
        
        rsi_vals = [50.0] * n
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        if avg_loss == 0:
            rsi_vals[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_vals[period] = 100.0 - (100.0 / (1.0 + rs))
            
        for i in range(period, n - 1):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_vals[i + 1] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_vals[i + 1] = 100.0 - (100.0 / (1.0 + rs))
                
        return rsi_vals

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)

        if len(closes) < self.lookback + self.rsi_period + 1:
            return None

        # Manage open position
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar
            rsi_series = self._rsi_series(closes, self.rsi_period)
            curr_rsi = rsi_series[-1] if rsi_series else 50.0

            # Time-based stop or RSI normalization exit
            if bars_in_pos >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_exit", "bars_held": bars_in_pos, "rsi": round(curr_rsi, 2)}
                )

            if ctx.position_direction() == "long" and curr_rsi >= 55.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_normalized_long", "rsi": round(curr_rsi, 2), "bars_held": bars_in_pos}
                )

            if ctx.position_direction() == "short" and curr_rsi <= 45.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_normalized_short", "rsi": round(curr_rsi, 2), "bars_held": bars_in_pos}
                )

            return None

        # Check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Avoid entries in extreme crisis volatility
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi_series = self._rsi_series(closes, self.rsi_period)
        if not rsi_series or len(rsi_series) < self.lookback:
            return None

        # Identify local pivots over lookback window (ignoring the active bar -1)
        # Search for pivot high / low in the window [T-28 to T-2]
        window_len = 25
        w_closes = closes[-window_len:]
        w_lows = lows[-window_len:]
        w_highs = highs[-window_len:]
        w_rsi = rsi_series[-window_len:]

        # Current confirmation values (last confirmed bar is -1)
        curr_low = w_lows[-1]
        curr_high = w_highs[-1]
        curr_rsi = w_rsi[-1]

        # Find swing low pivot (minimum low in older section: -window_len to -5)
        prior_lows = w_lows[:-5]
        prior_rsi = w_rsi[:-5]
        min_prior_low = min(prior_lows)
        min_idx = prior_lows.index(min_prior_low)
        rsi_at_min_low = prior_rsi[min_idx]

        # Bullish Divergence: Current low < prior low, but Current RSI > prior RSI, with RSI oversold context
        if curr_low < min_prior_low and curr_rsi > (rsi_at_min_low + 2.0) and curr_rsi < 42.0:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "curr_low": curr_low,
                    "prior_low": min_prior_low,
                    "curr_rsi": round(curr_rsi, 2),
                    "prior_rsi": round(rsi_at_min_low, 2),
                }
            )

        # Find swing high pivot (maximum high in older section: -window_len to -5)
        prior_highs = w_highs[:-5]
        max_prior_high = max(prior_highs)
        max_idx = prior_highs.index(max_prior_high)
        rsi_at_max_high = prior_rsi[max_idx]

        # Bearish Divergence: Current high > prior high, but Current RSI < prior RSI, with RSI overbought context
        if curr_high > max_prior_high and curr_rsi < (rsi_at_max_high - 2.0) and curr_rsi > 58.0:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "curr_high": curr_high,
                    "prior_high": max_prior_high,
                    "curr_rsi": round(curr_rsi, 2),
                    "prior_rsi": round(rsi_at_max_high, 2),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index