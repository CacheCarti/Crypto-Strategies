from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBReversion(Strategy):
    METADATA = {
        "name": "SolPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Compute current Bollinger Bands
        bb_curr = self._bollinger(closes[-self.period:], self.period, self.num_std)
        # Compute previous Bollinger Bands
        bb_prev = self._bollinger(closes[-self.period - 1:-1], self.period, self.num_std)

        if bb_curr is None or bb_prev is None:
            return None

        mid, upper, lower = bb_curr
        bandwidth = upper - lower
        if bandwidth <= 0:
            return None

        mid_prev, upper_prev, lower_prev = bb_prev
        bw_prev = upper_prev - lower_prev
        if bw_prev <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        pct_b_curr = (curr_close - lower) / bandwidth
        pct_b_prev = (prev_close - lower_prev) / bw_prev

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion exit at the mid-band
            if pos_dir == "long" and curr_close >= mid:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_tp_mid_band", "close": curr_close, "mid": mid, "pct_b": pct_b_curr})
            elif pos_dir == "short" and curr_close <= mid:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_tp_mid_band", "close": curr_close, "mid": mid, "pct_b": pct_b_curr})
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: skip entries during extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility-based structural risk sizing using ATR
        highs = ctx.highs(16)
        lows = ctx.lows(16)
        atr = self._atr(highs, lows, closes[-16:], period=14)
        if atr is None or curr_close <= 0:
            return None

        atr_bps = (atr / curr_close) * 10000.0
        # Dynamic structural stop placed at 1.6x ATR to protect against runaway expansion
        dyn_sl_bps = max(180.0, min(380.0, atr_bps * 1.6))
        # Mid-band distance proxy for target profit
        tp_bps = max(180.0, min(450.0, (abs(curr_close - mid) / curr_close) * 10000.0))

        # Long Entry: Previous close fell outside lower band (pct_b < 0.0), current close reclaimed inside (pct_b >= 0.0 and <= 0.4)
        if pct_b_prev < 0.0 and 0.0 <= pct_b_curr <= 0.40:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_lower_reclaim",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "close": curr_close,
                    "lower": lower,
                    "mid": mid,
                    "atr_bps": atr_bps
                }
            )

        # Short Entry: Previous close pierced upper band (pct_b > 1.0), current close closed back inside (pct_b <= 1.0 and >= 0.6)
        if pct_b_prev > 1.0 and 0.60 <= pct_b_curr <= 1.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pctb_upper_reclaim",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "close": curr_close,
                    "upper": upper,
                    "mid": mid,
                    "atr_bps": atr_bps
                }
            )

        return None