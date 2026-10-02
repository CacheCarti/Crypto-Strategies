from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class SolBollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "SOL Bollinger Squeeze Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 120,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std_mult = 2.1
        self.squeeze_lookback = 100
        self.squeeze_percentile = 0.20
        self.rsi_period = 14
        self.vol_period = 20
        self.cooldown_bars = 14
        self.last_exit_bar = -999

    def _bollinger_bands(self, closes: List[float], period: int, mult: float):
        if len(closes) < period:
            return None, None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        upper = mean + mult * std
        lower = mean - mult * std
        bandwidth = (upper - lower) / mean if mean > 0 else 0.0
        return mean, upper, lower, bandwidth

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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
        needed_history = self.squeeze_lookback + self.bb_period + 5
        closes = ctx.closes(needed_history)
        volumes = ctx.volumes(self.vol_period + 2)

        if len(closes) < needed_history or len(volumes) < self.vol_period:
            return None

        # Current & Previous Bollinger Bands
        mean, upper, lower, bandwidth = self._bollinger_bands(
            closes, self.bb_period, self.bb_std_mult
        )
        prev_mean, prev_upper, prev_lower, _ = self._bollinger_bands(
            closes[:-1], self.bb_period, self.bb_std_mult
        )

        if (
            mean is None
            or upper is None
            or lower is None
            or prev_upper is None
            or prev_lower is None
        ):
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        prev_close = closes[-2]

        # Active Position Exit Rules
        if ctx.has_position():
            direction = ctx.position_direction()

            # Long exit: clean breakdown below middle band or severe overbought exhaustion
            if direction == "long":
                if current_close < mean or rsi >= 78.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_exit_mean_or_overbought",
                            "close": current_close,
                            "bb_mean": mean,
                            "rsi": rsi,
                        },
                    )

            # Short exit: clean breakout above middle band or severe oversold bounce
            elif direction == "short":
                if current_close > mean or rsi <= 22.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_exit_mean_or_oversold",
                            "close": current_close,
                            "bb_mean": mean,
                            "rsi": rsi,
                        },
                    )

            return None

        # Hard Cooldown Guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Rolling Bandwidth History across lookback
        bandwidth_history: List[float] = []
        for i in range(len(closes) - self.squeeze_lookback, len(closes)):
            sub_closes = closes[: i + 1]
            _, _, _, bw = self._bollinger_bands(
                sub_closes, self.bb_period, self.bb_std_mult
            )
            if bw is not None:
                bandwidth_history.append(bw)

        if len(bandwidth_history) < self.squeeze_lookback:
            return None

        # Squeeze threshold: tight 20th percentile of lookback window
        sorted_bw = sorted(bandwidth_history)
        q_idx = int(len(sorted_bw) * self.squeeze_percentile)
        squeeze_threshold = sorted_bw[q_idx]

        # Squeeze must have occurred within the last 5 bars
        recent_bandwidths = bandwidth_history[-5:]
        was_in_squeeze = any(bw <= squeeze_threshold for bw in recent_bandwidths)
        if not was_in_squeeze:
            return None

        # Volume confirmation filter
        vol_sma = sum(volumes[-self.vol_period:]) / self.vol_period
        vol_ratio = ctx.bar.volume / vol_sma if vol_sma > 0 else 1.0
        if vol_ratio < 1.10:
            return None

        # FRESH Bullish Breakout: Previous bar was inside bands, current bar cleanly closes above upper band
        is_fresh_long_breakout = (
            prev_close <= prev_upper
            and current_close > upper
            and current_close > current_open
            and 52.0 <= rsi <= 72.0
        )

        if is_fresh_long_breakout:
            confidence = min(0.85, 0.60 + (vol_ratio - 1.0) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bollinger_squeeze_bullish_breakout",
                    "close": current_close,
                    "upper_band": upper,
                    "bandwidth": bandwidth,
                    "squeeze_threshold": squeeze_threshold,
                    "vol_ratio": vol_ratio,
                    "rsi": rsi,
                },
            )

        # FRESH Bearish Breakout: Previous bar was inside bands, current bar cleanly closes below lower band
        is_fresh_short_breakout = (
            prev_close >= prev_lower
            and current_close < lower
            and current_close < current_open
            and 28.0 <= rsi <= 48.0
        )

        if is_fresh_short_breakout:
            confidence = min(0.85, 0.60 + (vol_ratio - 1.0) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bollinger_squeeze_bearish_breakout",
                    "close": current_close,
                    "lower_band": lower,
                    "bandwidth": bandwidth,
                    "squeeze_threshold": squeeze_threshold,
                    "vol_ratio": vol_ratio,
                    "rsi": rsi,
                },
            )

        return None