from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBReversion(Strategy):
    METADATA = {
        "name": "SolPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.atr_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

    def _bollinger(self, closes: list, period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14):
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
        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)

        if len(closes) < self.period + 2 or len(highs) < self.atr_period + 2:
            return None

        # Filter crisis or extreme market conditions
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Current bar Bollinger metrics
        mid, upper, lower = self._bollinger(closes[:-1], self.period, self.num_std) # prior bar bands
        curr_mid, curr_upper, curr_lower = self._bollinger(closes, self.period, self.num_std)
        if None in (mid, upper, lower, curr_mid, curr_upper, curr_lower):
            return None

        band_width = upper - lower
        curr_band_width = curr_upper - curr_lower
        if band_width <= 0 or curr_band_width <= 0:
            return None

        prev_close = closes[-2]
        curr_close = closes[-1]

        prev_pct_b = (prev_close - lower) / band_width
        curr_pct_b = (curr_close - curr_lower) / curr_band_width

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Volatility-adjusted stop-loss & take-profit in basis points
        sl_bps = max(180.0, min(450.0, (atr * 1.8 / curr_close) * 10000.0))
        tp_bps = max(220.0, min(550.0, (atr * 2.2 / curr_close) * 10000.0))

        # Handle active position management (Mid-band target, time stop)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Mid-band target exit
            if pos_dir == "long" and curr_close >= curr_mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_midband_reversion",
                    "curr_close": curr_close,
                    "mid": curr_mid,
                    "bars_held": bars_held
                })
            elif pos_dir == "short" and curr_close <= curr_mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_midband_reversion",
                    "curr_close": curr_close,
                    "mid": curr_mid,
                    "bars_held": bars_held
                })

            # Time stop exit if thesis stalls
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "timed_exit_max_bars",
                    "bars_held": bars_held
                })
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long: Previous bar penetrated below lower band (pct_b < 0), current bar closes back inside (> 0 and < 0.3)
        if prev_pct_b < 0.0 and 0.0 < curr_pct_b < 0.35:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "pctb_lower_rejection",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "atr_bps": round((atr / curr_close) * 10000.0, 1),
                }
            )

        # Short: Previous bar penetrated above upper band (pct_b > 1.0), current bar closes back inside (< 1.0 and > 0.65)
        if prev_pct_b > 1.0 and 0.65 < curr_pct_b < 1.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "pctb_upper_rejection",
                    "prev_pct_b": round(prev_pct_b, 3),
                    "curr_pct_b": round(curr_pct_b, 3),
                    "atr_bps": round((atr / curr_close) * 10000.0, 1),
                }
            )

        return None