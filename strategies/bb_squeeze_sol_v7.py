from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class SolBollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "Solana Bollinger Squeeze Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 120,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.lookback_bars = 100
        self.squeeze_quantile_thresh = 0.15
        self.cooldown_bars = 12
        self.last_exit_bar = -100

    def _calc_bb(self, values: List[float], period: int, num_std: float):
        if len(values) < period:
            return None, None, None
        sub = values[-period:]
        mean = sum(sub) / period
        variance = sum((x - mean) ** 2 for x in sub) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
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
        req_bars = self.lookback_bars + self.bb_period + 10
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(req_bars)
        volumes = ctx.volumes(30)
        if len(closes) < req_bars or len(volumes) < 20:
            return None

        # Compute historical Bandwidth series to detect true low-volatility squeezes
        bbw_history: List[float] = []
        for i in range(len(closes) - self.lookback_bars, len(closes)):
            sub_window = closes[: i + 1]
            m, u, l = self._calc_bb(sub_window, self.bb_period, self.bb_std)
            if m is not None and m > 0.0 and u is not None and l is not None:
                bbw = (u - l) / m
                bbw_history.append(bbw)

        if len(bbw_history) < self.lookback_bars:
            return None

        curr_mean, curr_upper, curr_lower = self._calc_bb(closes, self.bb_period, self.bb_std)
        prev_closes = closes[:-1]
        prev_mean, prev_upper, prev_lower = self._calc_bb(prev_closes, self.bb_period, self.bb_std)

        if curr_mean is None or prev_mean is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        rsi_val = self._rsi(closes, period=14) or 50.0

        # Position management / Exit check
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if current_close < curr_mean or rsi_val >= 80.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_midline_cross_or_overbought",
                            "rsi": round(rsi_val, 2),
                            "close": current_close,
                            "midline": round(curr_mean, 2),
                        },
                    )
            elif direction == "short":
                if current_close > curr_mean or rsi_val <= 20.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_midline_cross_or_oversold",
                            "rsi": round(rsi_val, 2),
                            "close": current_close,
                            "midline": round(curr_mean, 2),
                        },
                    )
            return None

        # Mandatory post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip during extreme crisis spikes to avoid whipsaws
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Stricter squeeze detection: lowest 15th percentile bandwidth over lookback
        recent_bandwidths = bbw_history[-6:-1]
        sorted_history = sorted(bbw_history)
        cutoff_val = sorted_history[int(len(sorted_history) * self.squeeze_quantile_thresh)]

        was_squeezed = any(bw <= cutoff_val for bw in recent_bandwidths)
        if not was_squeezed:
            return None

        # Volume expansion filter: current volume must exceed recent 20-bar SMA
        avg_vol = sum(volumes[-20:]) / 20.0
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        if vol_ratio < 1.15:
            return None

        current_bbw = (curr_upper - curr_lower) / curr_mean

        # High-conviction breakout triggers
        long_breakout = prev_close <= prev_upper and current_close > curr_upper and rsi_val >= 57.0
        short_breakout = prev_close >= prev_lower and current_close < curr_lower and rsi_val <= 43.0

        if long_breakout:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "squeeze_expansion_upper_breakout",
                    "bbw": round(current_bbw, 5),
                    "bbw_cutoff": round(cutoff_val, 5),
                    "rsi": round(rsi_val, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "upper_band": round(curr_upper, 2),
                    "close": current_close,
                },
            )

        if short_breakout:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "squeeze_expansion_lower_breakout",
                    "bbw": round(current_bbw, 5),
                    "bbw_cutoff": round(cutoff_val, 5),
                    "rsi": round(rsi_val, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "lower_band": round(curr_lower, 2),
                    "close": current_close,
                },
            )

        return None