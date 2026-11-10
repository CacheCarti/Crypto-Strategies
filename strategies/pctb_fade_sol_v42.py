from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeReversion(Strategy):
    METADATA = {
        "name": "SolPctBFadeReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.num_std = 2.0
        self.atr_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None, None, None
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
        closes = ctx.closes(self.bb_period + 3)
        highs = ctx.highs(self.atr_period + 3)
        lows = ctx.lows(self.atr_period + 3)

        if len(closes) < self.bb_period + 2:
            return None

        # Bollinger Bands for previous bar (to check prior break)
        prev_closes = closes[:-1]
        p_mid, p_upper, p_lower = self._bollinger(prev_closes, self.bb_period, self.num_std)
        # Bollinger Bands for current bar
        c_mid, c_upper, c_lower = self._bollinger(closes, self.bb_period, self.num_std)

        if p_mid is None or c_mid is None:
            return None

        p_band_width = p_upper - p_lower
        c_band_width = c_upper - c_lower
        if p_band_width <= 0 or c_band_width <= 0:
            return None

        p_close = prev_closes[-1]
        c_close = closes[-1]

        prev_pct_b = (p_close - p_lower) / p_band_width
        curr_pct_b = (c_close - c_lower) / c_band_width

        # Check existing positions for mid-band reversion exit
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and c_close >= c_mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "pctb_reversion_mid_reached_long",
                    "curr_close": c_close,
                    "mid_band": c_mid,
                    "curr_pct_b": curr_pct_b
                })
            elif direction == "short" and c_close <= c_mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "pctb_reversion_mid_reached_short",
                    "curr_close": c_close,
                    "mid_band": c_mid,
                    "curr_pct_b": curr_pct_b
                })
            return None

        # Cooldown guard to avoid rapid overtrading and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety: avoid entering fades during full market crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Volatility normalization: ensure bands are sufficiently wide to overcome 12 bps friction
        bandwidth_bps = (c_band_width / c_mid) * 10000.0
        if bandwidth_bps < 120.0:
            return None

        # Entry logic: failed breakout re-entering the band
        # Long: previous close below lower band (<0.0) and current close back inside (>0.05 to ensure clear rejection)
        if prev_pct_b < 0.0 and curr_pct_b > 0.05 and curr_pct_b < 0.40:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_pctb_breakdown_reclaimed",
                    "prev_pct_b": prev_pct_b,
                    "curr_pct_b": curr_pct_b,
                    "lower_band": c_lower,
                    "mid_band": c_mid,
                    "bandwidth_bps": bandwidth_bps
                }
            )

        # Short: previous close above upper band (>1.0) and current close back inside (<0.95 to ensure clear rejection)
        if prev_pct_b > 1.0 and curr_pct_b < 0.95 and curr_pct_b > 0.60:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_pctb_breakout_reclaimed",
                    "prev_pct_b": prev_pct_b,
                    "curr_pct_b": curr_pct_b,
                    "upper_band": c_upper,
                    "mid_band": c_mid,
                    "bandwidth_bps": bandwidth_bps
                }
            )

        return None