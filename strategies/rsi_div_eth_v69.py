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
        self.pivot_window = 3
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
            
        rsi_series = []
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(needed)
        lows = ctx.lows(needed)
        highs = ctx.highs(needed)
        
        if len(closes) < needed:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback:
            return None

        curr_rsi = rsi_vals[-1]

        # Manage open position exit logic (normalization)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_rsi >= 55.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "rsi_normalized_long", "rsi": curr_rsi}
                )
            elif pos_dir == "short" and curr_rsi <= 45.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "rsi_normalized_short", "rsi": curr_rsi}
                )
            return None

        # Check market regime to avoid taking divergence counter to severe stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Look for local pivot extrema over lookback
        w = self.pivot_window
        pivot_lows = []
        pivot_highs = []
        
        # We search within recent lookback, leaving margin for pivot confirmation
        start_idx = len(closes) - self.lookback
        end_idx = len(closes) - w

        rsi_offset = len(closes) - len(rsi_vals)

        for i in range(start_idx, end_idx):
            # Check pivot low
            if all(lows[i] < lows[i - k] for k in range(1, w + 1)) and \
               all(lows[i] <= lows[i + k] for k in range(1, w + 1)):
                r_idx = i - rsi_offset
                if 0 <= r_idx < len(rsi_vals):
                    pivot_lows.append((i, lows[i], rsi_vals[r_idx]))

            # Check pivot high
            if all(highs[i] > highs[i - k] for k in range(1, w + 1)) and \
               all(highs[i] >= highs[i + k] for k in range(1, w + 1)):
                r_idx = i - rsi_offset
                if 0 <= r_idx < len(rsi_vals):
                    pivot_highs.append((i, highs[i], rsi_vals[r_idx]))

        # Bullish Divergence check: price lower low, RSI higher low
        if len(pivot_lows) >= 2:
            prev_idx, prev_low, prev_rsi = pivot_lows[-2]
            curr_idx, curr_low, curr_rsi_piv = pivot_lows[-1]
            
            # Ensure recent pivot was established within the last 6 bars
            if (len(closes) - 1 - curr_idx) <= 6:
                if curr_low < prev_low and curr_rsi_piv > prev_rsi:
                    if curr_rsi < 48.0:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bullish_rsi_divergence",
                                "prev_low": prev_low,
                                "curr_low": curr_low,
                                "prev_rsi": prev_rsi,
                                "curr_rsi_pivot": curr_rsi_piv,
                                "current_rsi": curr_rsi
                            }
                        )

        # Bearish Divergence check: price higher high, RSI lower high
        if len(pivot_highs) >= 2:
            prev_idx, prev_high, prev_rsi = pivot_highs[-2]
            curr_idx, curr_high, curr_rsi_piv = pivot_highs[-1]

            if (len(closes) - 1 - curr_idx) <= 6:
                if curr_high > prev_high and curr_rsi_piv < prev_rsi:
                    if curr_rsi > 52.0:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bearish_rsi_divergence",
                                "prev_high": prev_high,
                                "curr_high": curr_high,
                                "prev_rsi": prev_rsi,
                                "curr_rsi_pivot": curr_rsi_piv,
                                "current_rsi": curr_rsi
                            }
                        )

        return None