from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List

class RsiDivergenceEventStrategy(Strategy):
    METADATA = {
        "name": "RsiDivergenceEventStrategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 3
        self.cooldown_bars = 12
        self.max_hold_bars = 24
        
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.last_signal_bar = -999

    def _calc_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
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
        
        rsi_series = [50.0] * n
        avg_gain = sum(gains[1:period + 1]) / period
        avg_loss = sum(losses[1:period + 1]) / period
        
        if avg_loss == 0.0:
            rsi_series[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_series[period] = 100.0 - (100.0 / (1.0 + rs))
            
        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_series[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_series[i] = 100.0 - (100.0 / (1.0 + rs))
                
        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < len(closes):
            return None

        current_rsi = rsi_series[-1]

        # Manage open position
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Time stop
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_stop_exit",
                    "bars_held": bars_held,
                    "rsi": current_rsi
                })

            # RSI normalization exit
            if direction == "long" and current_rsi >= 58.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "rsi_reversion_long_exit",
                    "bars_held": bars_held,
                    "rsi": current_rsi
                })
            elif direction == "short" and current_rsi <= 42.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "rsi_reversion_short_exit",
                    "bars_held": bars_held,
                    "rsi": current_rsi
                })
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_signal_bar) < self.cooldown_bars:
            return None

        # Regime filter: avoid market crisis
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.65:
            return None

        # Pivot detection over lookback
        w = self.pivot_window
        eval_len = self.lookback
        offset = len(closes) - eval_len

        # Candidate pivots: index i is a pivot if it is local extremum within [i - w, i + w]
        low_pivots = []
        high_pivots = []

        for i in range(offset + w, len(closes) - w):
            p = closes[i]
            r = rsi_series[i]
            
            is_low = True
            is_high = True
            for k in range(i - w, i + w + 1):
                if closes[k] < p:
                    is_low = False
                if closes[k] > p:
                    is_high = False
            
            if is_low:
                low_pivots.append((i, p, r))
            if is_high:
                high_pivots.append((i, p, r))

        # Check Bullish Divergence: Recent low pivot has lower price but higher RSI
        if len(low_pivots) >= 2:
            prev_low = low_pivots[-2]
            curr_low = low_pivots[-1]
            
            # Ensure the recent pivot is sufficiently fresh
            bars_since_pivot = (len(closes) - 1) - curr_low[0]
            if bars_since_pivot <= 4:
                price_lower = curr_low[1] < prev_low[1] * 0.9985
                rsi_higher = curr_low[2] > prev_low[2] + 1.5
                rsi_oversold_zone = curr_low[2] < 40.0
                
                if price_lower and rsi_higher and rsi_oversold_zone:
                    self.entry_bar = ctx.bar_index
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "pivot_rsi": curr_low[2],
                            "prev_pivot_rsi": prev_low[2],
                            "pivot_price": curr_low[1],
                            "prev_pivot_price": prev_low[1]
                        }
                    )

        # Check Bearish Divergence: Recent high pivot has higher price but lower RSI
        if len(high_pivots) >= 2:
            prev_high = high_pivots[-2]
            curr_high = high_pivots[-1]
            
            bars_since_pivot = (len(closes) - 1) - curr_high[0]
            if bars_since_pivot <= 4:
                price_higher = curr_high[1] > prev_high[1] * 1.0015
                rsi_lower = curr_high[2] < prev_high[2] - 1.5
                rsi_overbought_zone = curr_high[2] > 60.0
                
                if price_higher and rsi_lower and rsi_overbought_zone:
                    self.entry_bar = ctx.bar_index
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "pivot_rsi": curr_high[2],
                            "prev_pivot_rsi": prev_high[2],
                            "pivot_price": curr_high[1],
                            "prev_pivot_price": prev_high[1]
                        }
                    )

        return None