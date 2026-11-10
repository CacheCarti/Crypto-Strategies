from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceStructural(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceStructural",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 36000,  # ~10 hours target hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.rsi_period = 14
        self.pivot_window = 3

    def _calc_rsi_series(self, closes: list, count: int) -> list:
        # Returns the last `count` RSI values efficiently
        needed = self.rsi_period + count + 1
        if len(closes) < needed:
            return []
        
        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0.0) for d in diffs]
        losses = [max(-d, 0.0) for d in diffs]
        
        start_idx = len(diffs) - count
        rsi_series = []
        for i in range(start_idx, len(diffs)):
            subset_g = gains[i - self.rsi_period + 1:i + 1]
            subset_l = losses[i - self.rsi_period + 1:i + 1]
            avg_g = sum(subset_g) / self.rsi_period
            avg_l = sum(subset_l) / self.rsi_period
            if avg_l == 0:
                rsi_series.append(100.0)
            else:
                rs = avg_g / avg_l
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        if len(closes) < 45:
            return None

        # Manage open position exits
        if ctx.has_position():
            curr_rsi_list = self._calc_rsi_series(closes, 1)
            curr_rsi = curr_rsi_list[-1] if curr_rsi_list else 50.0
            pos_dir = ctx.position_direction()

            # Dynamic structural mean-reversion exit
            if pos_dir == "long" and curr_rsi >= 60.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_normalized_long", "rsi": curr_rsi}
                )
            elif pos_dir == "short" and curr_rsi <= 40.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_normalized_short", "rsi": curr_rsi}
                )
            return None

        # Mandatory post-exit cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Regime protection: skip entries during high turbulence or market meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate historical RSI over the last 30 bars
        rsi_history = self._calc_rsi_series(closes, 30)
        if len(rsi_history) < 30:
            return None

        c_window = closes[-30:]
        pivots_low = []
        pivots_high = []
        k = self.pivot_window

        # Identify local extrema with a 3-bar confirmation window
        for i in range(k, 30 - k):
            c_val = c_window[i]
            is_low = all(c_val <= c_window[j] for j in range(i - k, i + k + 1) if j != i)
            is_high = all(c_val >= c_window[j] for j in range(i - k, i + k + 1) if j != i)

            if is_low:
                pivots_low.append((i, c_val, rsi_history[i]))
            if is_high:
                pivots_high.append((i, c_val, rsi_history[i]))

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes[-20:], period=14)
        atr_bps = (atr / closes[-1]) * 10000.0 if atr else 200.0

        # Dynamic volatility stop calculation (ensures stop respects structural giveback limits)
        dyn_sl = max(180.0, min(350.0, atr_bps * 1.6))
        dyn_tp = max(300.0, min(550.0, dyn_sl * 1.6))

        # Bullish Divergence check (Price Lower Low, RSI Higher Low)
        if len(pivots_low) >= 2:
            prev_idx, prev_price, prev_rsi = pivots_low[-2]
            curr_idx, curr_price, curr_rsi = pivots_low[-1]

            # Require recent second pivot and clear oversold involvement
            if (30 - 1 - curr_idx) <= 4 and (curr_idx - prev_idx) >= 4:
                if curr_price < prev_price and curr_rsi > prev_rsi and min(prev_rsi, curr_rsi) < 42.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=dyn_sl,
                        take_profit_bps=dyn_tp,
                        horizon_seconds=36000,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "prev_rsi": round(prev_rsi, 2),
                            "curr_rsi": round(curr_rsi, 2),
                            "prev_price": round(prev_price, 2),
                            "curr_price": round(curr_price, 2),
                            "atr_bps": round(atr_bps, 1),
                        }
                    )

        # Bearish Divergence check (Price Higher High, RSI Lower High)
        if len(pivots_high) >= 2:
            prev_idx, prev_price, prev_rsi = pivots_high[-2]
            curr_idx, curr_price, curr_rsi = pivots_high[-1]

            if (30 - 1 - curr_idx) <= 4 and (curr_idx - prev_idx) >= 4:
                if curr_price > prev_price and curr_rsi < prev_rsi and max(prev_rsi, curr_rsi) > 58.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=dyn_sl,
                        take_profit_bps=dyn_tp,
                        horizon_seconds=36000,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "prev_rsi": round(prev_rsi, 2),
                            "curr_rsi": round(curr_rsi, 2),
                            "prev_price": round(prev_price, 2),
                            "curr_price": round(curr_price, 2),
                            "atr_bps": round(atr_bps, 1),
                        }
                    )

        return None