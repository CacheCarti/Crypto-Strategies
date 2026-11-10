from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctbFade(Strategy):
    METADATA = {
        "name": "SolPctbFade",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger(self, closes: list, period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 2)
        if len(closes) < self.period + 2:
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute Bollinger Bands for current and previous bar
        mid_curr, up_curr, low_curr = self._bollinger(closes[-self.period:], self.period, self.num_std)
        mid_prev, up_prev, low_prev = self._bollinger(closes[-self.period - 1:-1], self.period, self.num_std)

        if mid_curr is None or mid_prev is None or up_curr == low_curr or up_prev == low_prev:
            return None

        pctb_curr = (closes[-1] - low_curr) / (up_curr - low_curr)
        pctb_prev = (closes[-2] - low_prev) / (up_prev - low_prev)

        # Dynamic exit at mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and closes[-1] >= mid_curr:
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": "long_hit_mid_band",
                    "pctb": round(pctb_curr, 4),
                    "mid_band": round(mid_curr, 2),
                    "close": round(closes[-1], 2)
                })
            elif pos_dir == "short" and closes[-1] <= mid_curr:
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": "short_hit_mid_band",
                    "pctb": round(pctb_curr, 4),
                    "mid_band": round(mid_curr, 2),
                    "close": round(closes[-1], 2)
                })
            return None

        # Filter extreme crisis regimes
        crisis = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # RSI confirmation to avoid one-bar whipsaws & strengthen conviction
        rsi_vals = ctx.closes(self.rsi_period + 2)
        rsi = self._rsi(rsi_vals, self.rsi_period)
        if rsi is None:
            return None

        # Long Setup: Was outside lower band (< 0.0), now closed firmly back inside (> 0.05 and < 0.40)
        if pctb_prev < 0.0 and 0.05 < pctb_curr < 0.45 and rsi < 42.0:
            return ctx.signal("long", confidence=0.72,
                              stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              metadata={
                                  "reason": "pctb_lower_reentry_rsi_oversold",
                                  "pctb_prev": round(pctb_prev, 4),
                                  "pctb_curr": round(pctb_curr, 4),
                                  "rsi": round(rsi, 2),
                                  "close": round(closes[-1], 2)
                              })

        # Short Setup: Was outside upper band (> 1.0), now closed firmly back inside (< 0.95 and > 0.55)
        if pctb_prev > 1.0 and 0.55 < pctb_curr < 0.95 and rsi > 58.0:
            return ctx.signal("short", confidence=0.72,
                              stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              metadata={
                                  "reason": "pctb_upper_reentry_rsi_overbought",
                                  "pctb_prev": round(pctb_prev, 4),
                                  "pctb_curr": round(pctb_curr, 4),
                                  "rsi": round(rsi, 2),
                                  "close": round(closes[-1], 2)
                              })

        return None