from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List

class RobustRsiDivergence(Strategy):
    METADATA = {
        "name": "Robust RSI Divergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 28
        self.cooldown_bars = 10
        self.last_exit_bar = -999

    def _calc_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        rsi_series = []
        for end_idx in range(period + 1, len(closes) + 1):
            sub = closes[:end_idx]
            gains, losses = [], []
            for i in range(1, len(sub)):
                diff = sub[i] - sub[i - 1]
                gains.append(max(diff, 0.0))
                losses.append(max(-diff, 0.0))
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback + self.rsi_period + 4
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Calculate aligned RSI series for the recent lookback window
        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback + 2:
            return None

        curr_rsi = rsi_series[-1]
        curr_price = ctx.bar.close

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            # Dynamic normalization exit: RSI crossing back through neutral
            if direction == "long" and curr_rsi > 55.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_long_exit", "rsi": curr_rsi})
            elif direction == "short" and curr_rsi < 45.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_normalized_short_exit", "rsi": curr_rsi})
            return None

        # Mandatory cooldown between trades to avoid friction drag
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Skip volatile or crisis regimes
        if ctx.regime in ("volatile", "crisis"):
            return None

        # Sub-slices corresponding to lookback window up to previous bar (confirming pivot)
        p_window = closes[-self.lookback:]
        r_window = rsi_series[-self.lookback:]

        # Find swing low pivot (Bullish Divergence)
        # Price: lower low formed recently vs older low in lookback window
        # RSI: higher low formed recently vs older RSI in lookback window
        # We split the lookback into two halves: prior base (earlier) and recent trough
        mid = len(p_window) // 2
        p_early, p_recent = p_window[:mid], p_window[mid:]
        r_early, r_recent = r_window[:mid], r_window[mid:]

        min_p_early, min_p_recent = min(p_early), min(p_recent)
        min_r_early, min_r_recent = min(r_early), min(r_recent)

        # Bullish divergence setup:
        # 1. Price made a distinctly lower low in recent half
        # 2. RSI made a distinctly higher low (momentum exhaustion)
        # 3. Overall RSI remains deeply oversold / turning (< 40)
        bullish_div = (min_p_recent < min_p_early * 0.995) and (min_r_recent > min_r_early + 2.0) and (curr_rsi < 40.0)

        if bullish_div:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=220.0,
                take_profit_bps=360.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "rsi": curr_rsi,
                    "min_r_recent": min_r_recent,
                    "min_r_early": min_r_early,
                    "price": curr_price,
                },
            )

        # Find swing high pivot (Bearish Divergence)
        max_p_early, max_p_recent = max(p_early), max(p_recent)
        max_r_early, max_r_recent = max(r_early), max(r_recent)

        # Bearish divergence setup:
        # 1. Price made a higher high in recent half
        # 2. RSI made a lower high (waning momentum)
        # 3. Overall RSI is elevated (> 60)
        bearish_div = (max_p_recent > max_p_early * 1.005) and (max_r_recent < max_r_early - 2.0) and (curr_rsi > 60.0)

        if bearish_div:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=220.0,
                take_profit_bps=360.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "rsi": curr_rsi,
                    "max_r_recent": max_r_recent,
                    "max_r_early": max_r_early,
                    "price": curr_price,
                },
            )

        return None