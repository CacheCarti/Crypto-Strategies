from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 2  # Local extremum over +/- 2 bars
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_trade = 0

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = []
        losses = []
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
            
        rsi_series = [50.0] * period
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars = self.lookback + self.rsi_period + 5
        closes = ctx.closes(total_bars)
        lows = ctx.lows(total_bars)
        highs = ctx.highs(total_bars)

        if len(closes) < total_bars:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback + 5:
            return None

        curr_rsi = rsi_series[-1]

        # Manage existing positions
        if ctx.has_position():
            self.bars_in_trade += 1
            direction = ctx.position_direction()
            
            # Normalization exit
            if direction == "long" and curr_rsi >= 58.0:
                return ctx.signal(
                    "flat", 
                    confidence=0.6, 
                    metadata={"reason": "rsi_reverted_bull_exit", "rsi": curr_rsi, "bars_held": self.bars_in_trade}
                )
            elif direction == "short" and curr_rsi <= 42.0:
                return ctx.signal(
                    "flat", 
                    confidence=0.6, 
                    metadata={"reason": "rsi_reverted_bear_exit", "rsi": curr_rsi, "bars_held": self.bars_in_trade}
                )
            
            # Horizon time stop
            if self.bars_in_trade >= 18:
                return ctx.signal(
                    "flat", 
                    confidence=0.5, 
                    metadata={"reason": "time_stop_exit", "rsi": curr_rsi, "bars_held": self.bars_in_trade}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        pw = self.pivot_window
        eval_idx = len(closes) - 1 - pw
        
        # Check if candle at eval_idx was a strictly confirmed local low
        is_pivot_low = (
            lows[eval_idx] <= min(lows[eval_idx - pw : eval_idx]) and 
            lows[eval_idx] <= min(lows[eval_idx + 1 : eval_idx + pw + 1])
        )
        
        # Check if candle at eval_idx was a strictly confirmed local high
        is_pivot_high = (
            highs[eval_idx] >= max(highs[eval_idx - pw : eval_idx]) and 
            highs[eval_idx] >= max(highs[eval_idx + 1 : eval_idx + pw + 1])
        )

        start_search = max(pw, eval_idx - self.lookback)

        # Bullish Divergence check
        if is_pivot_low and rsi_series[eval_idx] < 40.0:
            for j in range(eval_idx - pw - 1, start_search, -1):
                if (
                    lows[j] <= min(lows[j - pw : j]) and 
                    lows[j] <= min(lows[j + 1 : j + pw + 1])
                ):
                    # Prior pivot found: check price lower low & RSI higher low
                    if lows[eval_idx] < lows[j] and rsi_series[eval_idx] > rsi_series[j] + 1.5:
                        # Event boundary trigger: current bar must reverse above trigger candle's high
                        if ctx.bar.close > highs[eval_idx]:
                            return ctx.signal(
                                "long",
                                confidence=0.75,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "bullish_rsi_div_confirmed",
                                    "curr_rsi": curr_rsi,
                                    "p1_rsi": rsi_series[eval_idx],
                                    "p0_rsi": rsi_series[j],
                                    "p1_low": lows[eval_idx],
                                    "p0_low": lows[j]
                                }
                            )
                    break

        # Bearish Divergence check
        if is_pivot_high and rsi_series[eval_idx] > 60.0:
            for j in range(eval_idx - pw - 1, start_search, -1):
                if (
                    highs[j] >= max(highs[j - pw : j]) and 
                    highs[j] >= max(highs[j + 1 : j + pw + 1])
                ):
                    # Prior pivot found: check price higher high & RSI lower high
                    if highs[eval_idx] > highs[j] and rsi_series[eval_idx] < rsi_series[j] - 1.5:
                        # Event boundary trigger: current bar must reverse below trigger candle's low
                        if ctx.bar.close < lows[eval_idx]:
                            return ctx.signal(
                                "short",
                                confidence=0.75,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "bearish_rsi_div_confirmed",
                                    "curr_rsi": curr_rsi,
                                    "p1_rsi": rsi_series[eval_idx],
                                    "p0_rsi": rsi_series[j],
                                    "p1_high": highs[eval_idx],
                                    "p0_high": highs[j]
                                }
                            )
                    break

        return None