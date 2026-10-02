import math
from typing import Optional, Dict, Any
from domains.strategy_contract import Strategy, BarContext, Signal


class WickFillReversion(Strategy):
    METADATA = {
        "name": "WickFillReversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 24
        self.vol_period = 24
        self.rsi_period = 14
        self.wick_threshold = 0.65
        self.range_multiplier = 1.65
        self.volume_multiplier = 1.45
        self.cooldown_bars = 14
        self.max_hold_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_retrace_price = 0.0

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
        self.entry_bar = -999
        self.target_retrace_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + self.rsi_period + 5)
        highs = ctx.highs(self.range_period + 5)
        lows = ctx.lows(self.range_period + 5)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.range_period + self.rsi_period:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0

            # Retrace target exit (fade target achieved)
            if pos_dir == "long" and self.target_retrace_price > 0 and ctx.bar.close >= self.target_retrace_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "wick_retrace_target_hit_long",
                    "close": ctx.bar.close,
                    "target": self.target_retrace_price,
                    "bars_held": bars_held
                })
            elif pos_dir == "short" and self.target_retrace_price > 0 and ctx.bar.close <= self.target_retrace_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "wick_retrace_target_hit_short",
                    "close": ctx.bar.close,
                    "target": self.target_retrace_price,
                    "bars_held": bars_held
                })

            # Time horizon max hold exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_held_exit",
                    "close": ctx.bar.close,
                    "bars_held": bars_held
                })

            return None

        # Hard multi-bar cooldown after exit to eliminate overtrading and friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime protection - avoid fading chaotic volatile breakouts
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Baseline range & volume over completed prior bars
        ranges = [h - l for h, l in zip(highs[-self.range_period - 1:-1], lows[-self.range_period - 1:-1])]
        avg_range = sum(ranges) / len(ranges) if ranges else 0.0

        past_vols = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(past_vols) / len(past_vols) if past_vols else 0.0

        if avg_range <= 0 or avg_vol <= 0:
            return None

        bar_range = ctx.bar.high - ctx.bar.low
        if bar_range <= 0:
            return None

        upper_wick = ctx.bar.high - max(ctx.bar.open, ctx.bar.close)
        lower_wick = min(ctx.bar.open, ctx.bar.close) - ctx.bar.low

        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range
        range_expansion = bar_range / avg_range
        vol_expansion = ctx.bar.volume / avg_vol

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Strict outlier setup: Significant range expansion + clear volume confirmation
        if range_expansion >= self.range_multiplier and vol_expansion >= self.volume_multiplier:
            # Rejection of lows: long bottom wick + RSI non-overbought (< 48)
            if lower_wick_ratio >= self.wick_threshold and rsi < 48.0:
                self.entry_bar = ctx.bar_index
                self.target_retrace_price = ctx.bar.low + (bar_range * 0.75)
                confidence = min(0.85, 0.60 + 0.20 * (lower_wick_ratio - self.wick_threshold))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "exhaustion_lower_wick_reversal_long",
                        "lower_wick_ratio": round(lower_wick_ratio, 3),
                        "range_expansion": round(range_expansion, 2),
                        "vol_expansion": round(vol_expansion, 2),
                        "rsi": round(rsi, 2),
                        "target_price": round(self.target_retrace_price, 2)
                    }
                )

            # Rejection of highs: long top wick + RSI non-oversold (> 52)
            if upper_wick_ratio >= self.wick_threshold and rsi > 52.0:
                self.entry_bar = ctx.bar_index
                self.target_retrace_price = ctx.bar.high - (bar_range * 0.75)
                confidence = min(0.85, 0.60 + 0.20 * (upper_wick_ratio - self.wick_threshold))

                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "exhaustion_upper_wick_reversal_short",
                        "upper_wick_ratio": round(upper_wick_ratio, 3),
                        "range_expansion": round(range_expansion, 2),
                        "vol_expansion": round(vol_expansion, 2),
                        "rsi": round(rsi, 2),
                        "target_price": round(self.target_retrace_price, 2)
                    }
                )

        return None