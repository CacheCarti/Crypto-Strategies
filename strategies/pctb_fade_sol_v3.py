from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BollingerPercentBReversion(Strategy):
    METADATA = {
        "name": "Bollinger Percent B Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 24
        self.bb_std = 2.35
        self.rsi_period = 14
        self.cooldown_bars = 14
        self.last_exit_bar = -999

    def _bollinger_bands(self, closes, period: int, num_std: float):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        if len(closes) < self.bb_period + 2:
            return None

        # Calculate current Bollinger Bands & %B
        mid, upper, lower = self._bollinger_bands(closes, self.bb_period, self.bb_std)
        if mid is None or upper is None or lower is None or upper <= lower:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        band_width = upper - lower
        curr_pct_b = (curr_close - lower) / band_width

        # Calculate previous bar's Bollinger Bands & %B
        prev_mid, prev_upper, prev_lower = self._bollinger_bands(closes[:-1], self.bb_period, self.bb_std)
        if prev_mid is None or prev_upper is None or prev_lower is None or prev_upper <= prev_lower:
            return None

        prev_pct_b = (prev_close - prev_lower) / (prev_upper - prev_lower)
        rsi = self._rsi(closes, self.rsi_period)
        rsi_val = rsi if rsi is not None else 50.0

        # Market regime filter - avoid extreme market crises
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Position Management: Exit when reverting back through mid-band
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close >= mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_tp_mid_band_reached",
                        "close": curr_close,
                        "mid_band": mid,
                        "pct_b": curr_pct_b,
                    },
                )
            elif pos_dir == "short" and curr_close <= mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_tp_mid_band_reached",
                        "close": curr_close,
                        "mid_band": mid,
                        "pct_b": curr_pct_b,
                    },
                )
            return None

        # Hard Cooldown check after position exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry Filter: Minimum band separation to avoid low-volatility chop entries
        rel_width_bps = (band_width / mid) * 10000.0
        if rel_width_bps < 180.0:
            return None

        # Entry Conditions (Tighter 2.35-std threshold + confirmation filters)
        # Long Setup: Deep band pierce (%B was clearly negative), now recovered back inside with RSI oversold confirmation
        if prev_pct_b < -0.05 and curr_pct_b >= 0.02 and curr_pct_b < 0.40 and rsi_val <= 38.0:
            confidence = min(0.95, max(0.60, 0.60 + (38.0 - rsi_val) * 0.015))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_deep_lower_reversal",
                    "pct_b": curr_pct_b,
                    "prev_pct_b": prev_pct_b,
                    "close": curr_close,
                    "lower_band": lower,
                    "mid_band": mid,
                    "rsi": rsi_val,
                },
            )

        # Short Setup: Deep band pierce (%B was clearly above 1.05), now recovered back inside with RSI overbought confirmation
        if prev_pct_b > 1.05 and curr_pct_b <= 0.98 and curr_pct_b > 0.60 and rsi_val >= 62.0:
            confidence = min(0.95, max(0.60, 0.60 + (rsi_val - 62.0) * 0.015))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "pct_b_deep_upper_reversal",
                    "pct_b": curr_pct_b,
                    "prev_pct_b": prev_pct_b,
                    "close": curr_close,
                    "upper_band": upper,
                    "mid_band": mid,
                    "rsi": rsi_val,
                },
            )

        return None