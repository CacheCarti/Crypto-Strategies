from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WickFillReversion(Strategy):
    METADATA = {
        "name": "BTC Wick Fill Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 10800,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.min_wick_ratio = 0.65
        self.range_mult = 1.70
        self.vol_mult = 1.50
        self.cooldown_bars = 8
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_retrace_price = 0.0

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
        self.entry_bar = -999
        self.target_retrace_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.period + 10)
        highs = ctx.highs(self.period + 10)
        lows = ctx.lows(self.period + 10)
        volumes = ctx.volumes(self.period + 10)

        if len(closes) < self.period + 1:
            return None

        # Position management / Exit logic
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            current_price = ctx.bar.close
            pos_dir = ctx.position_direction()

            # Retracement exit: if price retraces into the target wick body
            if pos_dir == "long" and self.target_retrace_price > 0 and current_price >= self.target_retrace_price:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "wick_retrace_target_hit",
                        "bars_held": bars_held,
                        "current_price": current_price,
                        "target_price": self.target_retrace_price
                    }
                )
            elif pos_dir == "short" and self.target_retrace_price > 0 and current_price <= self.target_retrace_price:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "wick_retrace_target_hit",
                        "bars_held": bars_held,
                        "current_price": current_price,
                        "target_price": self.target_retrace_price
                    }
                )

            # Time-based exit for stagnant positions
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_held_reached",
                        "bars_held": bars_held,
                        "current_price": current_price
                    }
                )
            return None

        # Hard cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries during crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.50:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        avg_vol = sum(volumes[-self.period - 1:-1]) / self.period
        last_vol = ctx.bar.volume

        bar_high = ctx.bar.high
        bar_low = ctx.bar.low
        bar_open = ctx.bar.open
        bar_close = ctx.bar.close
        bar_range = bar_high - bar_low

        if bar_range <= 0:
            return None

        body_top = max(bar_open, bar_close)
        body_bottom = min(bar_open, bar_close)
        upper_wick = bar_high - body_top
        lower_wick = body_bottom - bar_low

        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range
        range_ratio = bar_range / atr
        vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 1.0

        # Strict outlier expansion filter: must be high volume and abnormally large range
        if range_ratio < self.range_mult or vol_ratio < self.vol_mult:
            return None

        # Long Setup: Extreme lower wick rejection on oversold/neutral momentum
        if lower_wick_ratio >= self.min_wick_ratio and rsi <= 45.0:
            self.entry_bar = ctx.bar_index
            self.target_retrace_price = bar_high - (bar_range * 0.20)
            confidence = min(0.95, 0.60 + (lower_wick_ratio - self.min_wick_ratio) * 1.2 + (range_ratio - self.range_mult) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_lower_wick_rejection",
                    "lower_wick_ratio": round(lower_wick_ratio, 3),
                    "range_to_atr": round(range_ratio, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "rsi": round(rsi, 1),
                    "price": bar_close,
                    "target_price": round(self.target_retrace_price, 2)
                }
            )

        # Short Setup: Extreme upper wick rejection on overbought/neutral momentum
        if upper_wick_ratio >= self.min_wick_ratio and rsi >= 55.0:
            self.entry_bar = ctx.bar_index
            self.target_retrace_price = bar_low + (bar_range * 0.20)
            confidence = min(0.95, 0.60 + (upper_wick_ratio - self.min_wick_ratio) * 1.2 + (range_ratio - self.range_mult) * 0.08)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_upper_wick_rejection",
                    "upper_wick_ratio": round(upper_wick_ratio, 3),
                    "range_to_atr": round(range_ratio, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "rsi": round(rsi, 1),
                    "price": bar_close,
                    "target_price": round(self.target_retrace_price, 2)
                }
            )

        return None