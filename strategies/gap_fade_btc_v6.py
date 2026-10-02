from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class WickFillReversion(Strategy):
    METADATA = {
        "name": "BTC Wick Fill Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 25,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.vol_period = 14
        self.rsi_period = 14
        self.wick_threshold = 0.48
        self.cooldown_bars = 3
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar_idx = -1
        self.entry_target_wick_level = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period=14):
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
        self.entry_bar_idx = -1
        self.entry_target_wick_level = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.atr_period + 5)
        highs = ctx.highs(self.atr_period + 5)
        lows = ctx.lows(self.atr_period + 5)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_high = ctx.bar.high
        bar_low = ctx.bar.low
        bar_open = ctx.bar.open
        bar_close = ctx.bar.close
        bar_range = bar_high - bar_low

        if bar_range <= 0:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx > 0 else 0
            pos_dir = ctx.position_direction()

            # Time-based hold exit
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_held_wick_timeout",
                        "bars_held": bars_held,
                        "close": bar_close,
                    },
                )

            # Target retrace level exit
            if pos_dir == "long" and self.entry_target_wick_level > 0 and bar_high >= self.entry_target_wick_level:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_wick_target_reached",
                        "target_level": round(self.entry_target_wick_level, 2),
                        "bar_high": round(bar_high, 2),
                        "bars_held": bars_held,
                    },
                )
            elif pos_dir == "short" and self.entry_target_wick_level > 0 and bar_low <= self.entry_target_wick_level:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_wick_target_reached",
                        "target_level": round(self.entry_target_wick_level, 2),
                        "bar_low": round(bar_low, 2),
                        "bars_held": bars_held,
                    },
                )

            return None

        # Mandatory cooldown after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Hard meltdown safety check
        if ctx.market.get("regime") == "MELTDOWN":
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.rsi_period)
        if atr is None or rsi is None or atr <= 0:
            return None

        vol_slice = volumes[-self.vol_period:]
        avg_vol = sum(vol_slice) / len(vol_slice) if len(vol_slice) > 0 else 1.0
        current_vol = ctx.bar.volume

        upper_wick = bar_high - max(bar_open, bar_close)
        lower_wick = min(bar_open, bar_close) - bar_low
        upper_wick_pct = upper_wick / bar_range
        lower_wick_pct = lower_wick / bar_range

        # Sane loosened activity filter: bar must have reasonable size relative to ATR
        if bar_range < 0.75 * atr:
            return None

        # Volume confirmation (moderate threshold)
        volume_ok = current_vol >= 0.85 * avg_vol
        if not volume_ok:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Bullish lower-wick rejection (buyers stepped in at lows, fade up)
        if lower_wick_pct >= self.wick_threshold and rsi < 72.0:
            confidence = min(0.85, max(0.55, 0.50 + lower_wick_pct * 0.4))
            self.entry_bar_idx = ctx.bar_index
            self.entry_target_wick_level = bar_close + (bar_range * 0.50)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "lower_wick_rejection_long",
                    "lower_wick_pct": round(lower_wick_pct, 3),
                    "range_to_atr": round(bar_range / atr, 2),
                    "volume_ratio": round(current_vol / avg_vol, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                },
            )

        # Bearish upper-wick rejection (sellers stepped in at highs, fade down)
        if upper_wick_pct >= self.wick_threshold and rsi > 28.0:
            confidence = min(0.85, max(0.55, 0.50 + upper_wick_pct * 0.4))
            self.entry_bar_idx = ctx.bar_index
            self.entry_target_wick_level = bar_close - (bar_range * 0.50)

            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "upper_wick_rejection_short",
                    "upper_wick_pct": round(upper_wick_pct, 3),
                    "range_to_atr": round(bar_range / atr, 2),
                    "volume_ratio": round(current_vol / avg_vol, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                },
            )

        return None