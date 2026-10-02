from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolPercentBReversion(Strategy):
    METADATA = {
        "name": "SolPercentBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 24
        self.bb_std = 2.35
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _bollinger(self, closes, period: int, num_std: float):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _rsi(self, closes, period: int = 14):
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
        closes = ctx.closes(self.bb_period + 6)
        if len(closes) < self.bb_period + 2:
            return None

        # Current bar Bollinger Bands & %B
        bb_curr = self._bollinger(closes, self.bb_period, self.bb_std)
        if bb_curr is None:
            return None
        mid, upper, lower = bb_curr
        band_width = upper - lower
        if band_width <= 0:
            return None

        curr_close = closes[-1]
        pct_b = (curr_close - lower) / band_width

        # Previous bar Bollinger Bands & %B
        bb_prev = self._bollinger(closes[:-1], self.bb_period, self.bb_std)
        if bb_prev is None:
            return None
        _, prev_upper, prev_lower = bb_prev
        prev_band_width = prev_upper - prev_lower
        if prev_band_width <= 0:
            return None

        prev_close = closes[-2]
        prev_pct_b = (prev_close - prev_lower) / prev_band_width

        rsi = self._rsi(closes, self.rsi_period)
        rsi_val = rsi if rsi is not None else 50.0

        # Position management: Take profit / mean-reversion exit at mid-band
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "long_mid_band_target_hit",
                        "close": curr_close,
                        "mid": mid,
                        "pct_b": pct_b,
                        "rsi": rsi_val,
                    },
                )
            elif direction == "short" and curr_close <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "short_mid_band_target_hit",
                        "close": curr_close,
                        "mid": mid,
                        "pct_b": pct_b,
                        "rsi": rsi_val,
                    },
                )
            return None

        # Hard multi-bar cooldown after exit to prevent overtrading & friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime & Crisis Filter: Avoid mean-reverting during strong market crisis or meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.40:
            return None

        # High-conviction Long Setup:
        # 1. Prior bar closed definitively below lower 2.35-std band (%B < -0.02)
        # 2. Current bar crosses back safely inside the band (0.05 <= %B <= 0.28)
        # 3. Oversold RSI confirmation (< 38.0)
        # 4. Not in a strong confirmed bear regime
        if (
            prev_pct_b < -0.02
            and 0.05 <= pct_b <= 0.28
            and rsi_val <= 38.0
            and trend_regime != "bear"
        ):
            confidence = min(0.92, max(0.65, 0.65 + (38.0 - rsi_val) * 0.02))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "percent_b_oversold_confirmed_reentry",
                    "prev_pct_b": prev_pct_b,
                    "curr_pct_b": pct_b,
                    "close": curr_close,
                    "lower_band": lower,
                    "mid_band": mid,
                    "rsi": rsi_val,
                    "trend_regime": trend_regime,
                },
            )

        # High-conviction Short Setup:
        # 1. Prior bar closed definitively above upper 2.35-std band (%B > 1.02)
        # 2. Current bar crosses back safely inside the band (0.72 <= %B <= 0.95)
        # 3. Overbought RSI confirmation (> 62.0)
        # 4. Not in a strong confirmed bull regime
        if (
            prev_pct_b > 1.02
            and 0.72 <= pct_b <= 0.95
            and rsi_val >= 62.0
            and trend_regime != "bull"
        ):
            confidence = min(0.92, max(0.65, 0.65 + (rsi_val - 62.0) * 0.02))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "percent_b_overbought_confirmed_reentry",
                    "prev_pct_b": prev_pct_b,
                    "curr_pct_b": pct_b,
                    "close": curr_close,
                    "upper_band": upper,
                    "mid_band": mid,
                    "rsi": rsi_val,
                    "trend_regime": trend_regime,
                },
            )

        return None