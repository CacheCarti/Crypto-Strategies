from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBFadeSelective(Strategy):
    METADATA = {
        "name": "SolPctBFadeSelective",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold on 1h bars
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
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

    def _rsi(self, closes: list, period: int = 14):
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
        closes = ctx.closes(self.bb_period + 5)
        highs = ctx.highs(self.bb_period + 5)
        lows = ctx.lows(self.bb_period + 5)

        if len(closes) < self.bb_period + 2:
            return None

        # Compute current bands
        mid, upper, lower = self._bollinger(closes, self.bb_period, self.num_std)
        if mid is None or upper == lower:
            return None

        band_width = upper - lower
        current_close = closes[-1]
        prev_close = closes[-2]
        pct_b = (current_close - lower) / band_width

        # Previous bands for boundary re-entry check
        prev_mid, prev_upper, prev_lower = self._bollinger(closes[:-1], self.bb_period, self.num_std)
        if prev_mid is None:
            return None

        prev_bw = prev_upper - prev_lower
        prev_pct_b = (prev_close - prev_lower) / prev_bw if prev_bw > 0 else 0.5

        # Check existing position exits (Mean reversion target: Mid-band cross)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_hit_midband", "pct_b": round(pct_b, 3), "price": current_close, "mid": round(mid, 2)}
                )
            elif pos_dir == "short" and current_close <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_hit_midband", "pct_b": round(pct_b, 3), "price": current_close, "mid": round(mid, 2)}
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety: avoid entering against panic/crisis volatility
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        # Re-entry conditions:
        # Long: Prior bar penetrated lower band (prev_pct_b < 0.0 or prev low pierced lower band),
        # and current close sharply reclaimed band (pct_b back between 0.05 and 0.40).
        # RSI must confirm stretched exhaustion (RSI < 38).
        is_long_setup = (
            (prev_pct_b < 0.0 or lows[-2] < prev_lower)
            and 0.05 <= pct_b <= 0.40
            and current_close > prev_close
            and rsi_val < 38.0
        )

        # Short: Prior bar penetrated upper band (prev_pct_b > 1.0 or prev high pierced upper band),
        # and current close fell back inside (pct_b between 0.60 and 0.95).
        # RSI must confirm stretched exhaustion (RSI > 62).
        is_short_setup = (
            (prev_pct_b > 1.0 or highs[-2] > prev_upper)
            and 0.60 <= pct_b <= 0.95
            and current_close < prev_close
            and rsi_val > 62.0
        )

        if is_long_setup:
            return ctx.signal(
                "long",
                confidence=0.80,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "pctb_lower_rejection_rsi_filter",
                    "pct_b": round(pct_b, 3),
                    "prev_pct_b": round(prev_pct_b, 3),
                    "rsi": round(rsi_val, 2),
                    "close": current_close,
                    "lower_band": round(lower, 2)
                }
            )

        if is_short_setup:
            return ctx.signal(
                "short",
                confidence=0.80,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "pctb_upper_rejection_rsi_filter",
                    "pct_b": round(pct_b, 3),
                    "prev_pct_b": round(prev_pct_b, 3),
                    "rsi": round(rsi_val, 2),
                    "close": current_close,
                    "upper_band": round(upper, 2)
                }
            )

        return None