from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 35
        self.cooldown_bars = 8
        self.last_trade_bar = -999

    def _rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        rsi_vals = []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        # Initial average
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi_vals.append(100.0 - (100.0 / (1.0 + rs)) if avg_loss > 0 else 100.0)

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_vals.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_vals

    def _find_pivots(self, series: List[float], span: int = 2) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        lows = []
        highs = []
        for i in range(span, len(series) - span):
            val = series[i]
            if all(val <= series[i - k] for k in range(1, span + 1)) and all(val <= series[i + k] for k in range(1, span + 1)):
                lows.append((i, val))
            if all(val >= series[i - k] for k in range(1, span + 1)) and all(val >= series[i + k] for k in range(1, span + 1)):
                highs.append((i, val))
        return lows, highs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)

        if len(closes) < self.lookback + self.rsi_period:
            return None

        # Build RSI series aligned with closes
        rsi_full = self._rsi_series(closes, self.rsi_period)
        if len(rsi_full) < self.lookback:
            return None

        current_rsi = rsi_full[-1]
        current_close = closes[-1]

        # Manage existing position
        if ctx.has_position():
            direction = ctx.position_direction()
            pos = ctx.position
            entry_price = pos.get("entry_price", current_close) if pos else current_close
            bars_held = ctx.bar_index - self.last_trade_bar

            # Normalize exits (RSI reverting to mean) or time stop (30 bars)
            if direction == "long" and (current_rsi >= 58.0 or bars_held >= 30):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_long_exit", "rsi": current_rsi})
            elif direction == "short" and (current_rsi <= 42.0 or bars_held >= 30):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_short_exit", "rsi": current_rsi})
            return None

        # Check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime safety
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime == "MELTDOWN":
            return None

        # Window for divergence detection
        window_closes = closes[-self.lookback:]
        window_lows = lows[-self.lookback:]
        window_highs = highs[-self.lookback:]
        window_rsi = rsi_full[-self.lookback:]

        # Identify local minima and maxima
        p_lows, p_highs = self._find_pivots(window_lows, span=2)
        r_lows, r_highs = self._find_pivots(window_rsi, span=2)

        # Bullish Divergence: Price Lower Low, RSI Higher Low
        # Focus on setups where the most recent pivot formed recently (within 5 bars)
        if len(p_lows) >= 2 and len(r_lows) >= 2:
            p1, p2 = p_lows[-2], p_lows[-1]
            # Ensure price pivots align reasonably with RSI lows
            if p2[0] >= self.lookback - 6 and (p2[0] - p1[0] >= 5):
                # Search corresponding RSI lows near the price pivot indices
                rsi_p1 = [val for idx, val in r_lows if abs(idx - p1[0]) <= 3]
                rsi_p2 = [val for idx, val in r_lows if abs(idx - p2[0]) <= 3]
                if rsi_p1 and rsi_p2:
                    # Divergence condition: price made lower low, RSI made higher low
                    if p2[1] < p1[1] and rsi_p2[-1] > rsi_p1[-1] and current_rsi < 48.0:
                        conf = min(0.9, 0.55 + (rsi_p2[-1] - rsi_p1[-1]) / 50.0)
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=conf,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bullish_rsi_divergence",
                                "p1_price": p1[1],
                                "p2_price": p2[1],
                                "rsi_p1": rsi_p1[-1],
                                "rsi_p2": rsi_p2[-1],
                                "current_rsi": current_rsi
                            }
                        )

        # Bearish Divergence: Price Higher High, RSI Lower High
        if len(p_highs) >= 2 and len(r_highs) >= 2:
            p1, p2 = p_highs[-2], p_highs[-1]
            if p2[0] >= self.lookback - 6 and (p2[0] - p1[0] >= 5):
                rsi_p1 = [val for idx, val in r_highs if abs(idx - p1[0]) <= 3]
                rsi_p2 = [val for idx, val in r_highs if abs(idx - p2[0]) <= 3]
                if rsi_p1 and rsi_p2:
                    # Divergence condition: price made higher high, RSI made lower high
                    if p2[1] > p1[1] and rsi_p2[-1] < rsi_p1[-1] and current_rsi > 52.0:
                        conf = min(0.9, 0.55 + (rsi_p1[-1] - rsi_p2[-1]) / 50.0)
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=conf,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bearish_rsi_divergence",
                                "p1_price": p1[1],
                                "p2_price": p2[1],
                                "rsi_p1": rsi_p1[-1],
                                "rsi_p2": rsi_p2[-1],
                                "current_rsi": current_rsi
                            }
                        )

        return None