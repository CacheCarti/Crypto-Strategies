from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class BollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "Bollinger Squeeze Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 340.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 120,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.lookback_window = 100
        self.squeeze_percentile = 0.15
        self.cooldown_bars = 16
        self.last_exit_bar = -100

    def _bollinger_slice(self, closes: List[float], period: int, num_std: float):
        if len(closes) < period:
            return None, None, None, 0.0
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        upper = mean + num_std * std
        lower = mean - num_std * std
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
        closes = ctx.closes(self.lookback_window + self.bb_period + 5)
        if len(closes) < self.lookback_window + self.bb_period:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Avoid entries and clean exit during extreme turmoil
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.40:
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": crisis_score},
                )
            return None

        # Current and previous Bollinger Bands
        curr_mean, curr_upper, curr_lower, curr_bw = self._bollinger_slice(
            closes, self.bb_period, self.bb_std
        )
        prev_mean, prev_upper, prev_lower, prev_bw = self._bollinger_slice(
            closes[:-1], self.bb_period, self.bb_std
        )
        if curr_mean is None or prev_mean is None:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        # Manage existing position exits with wider room to avoid premature churn
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit when trend structure breaks significantly below midpoint with weak momentum
                if curr_close < curr_mean and rsi < 42.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_breakdown",
                            "close": curr_close,
                            "mid": curr_mean,
                            "rsi": rsi,
                        },
                    )
            elif pos_dir == "short":
                # Exit when price reclaims above midpoint with strong upward momentum
                if curr_close > curr_mean and rsi > 58.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_breakdown",
                            "close": curr_close,
                            "mid": curr_mean,
                            "rsi": rsi,
                        },
                    )
            return None

        # Mandatory cooldown to prevent overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate historical bandwidth distribution over the lookback window
        bandwidth_history = []
        for i in range(self.lookback_window):
            sub_closes = closes[: len(closes) - i]
            _, _, _, bw = self._bollinger_slice(sub_closes, self.bb_period, self.bb_std)
            bandwidth_history.append(bw)

        sorted_bws = sorted(bandwidth_history)
        threshold_idx = int(len(sorted_bws) * self.squeeze_percentile)
        squeeze_threshold = sorted_bws[threshold_idx]

        # Strict Squeeze: bandwidth in recent bars was tightly compressed and is now expanding with volume
        was_squeezed = any(bw <= squeeze_threshold for bw in bandwidth_history[1:5])
        is_expanding = curr_bw > prev_bw * 1.05

        if not (was_squeezed and is_expanding):
            return None

        volumes = ctx.volumes(20)
        vol_avg = sum(volumes) / len(volumes) if len(volumes) >= 20 else 1.0
        curr_vol = ctx.bar.volume
        vol_confirmed = curr_vol >= vol_avg * 1.10

        # Bullish Breakout: fresh cross above upper band with high momentum and volume confirmation
        if curr_close > curr_upper and prev_close <= prev_upper and rsi >= 58.0 and vol_confirmed:
            if fg_index >= 25.0:
                conf = min(0.85, 0.60 + (rsi - 58.0) * 0.01)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bollinger_squeeze_bull_breakout",
                        "bandwidth": curr_bw,
                        "squeeze_threshold": squeeze_threshold,
                        "rsi": rsi,
                        "close": curr_close,
                        "upper_band": curr_upper,
                        "vol_ratio": round(curr_vol / vol_avg, 2) if vol_avg > 0 else 1.0,
                    },
                )

        # Bearish Breakout: fresh cross below lower band with downward momentum and volume confirmation
        if curr_close < curr_lower and prev_close >= prev_lower and rsi <= 42.0 and vol_confirmed:
            if fg_index <= 75.0:
                conf = min(0.85, 0.60 + (42.0 - rsi) * 0.01)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bollinger_squeeze_bear_breakout",
                        "bandwidth": curr_bw,
                        "squeeze_threshold": squeeze_threshold,
                        "rsi": rsi,
                        "close": curr_close,
                        "lower_band": curr_lower,
                        "vol_ratio": round(curr_vol / vol_avg, 2) if vol_avg > 0 else 1.0,
                    },
                )

        return None