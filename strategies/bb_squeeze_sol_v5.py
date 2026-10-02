import math
from typing import Optional, Dict, Any
from domains.strategy_contract import Strategy, BarContext, Signal

class SolBollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "SOL Bollinger Squeeze Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 130,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.squeeze_lookback = 100
        self.squeeze_quantile = 0.18
        self.cooldown_bars = 14
        self.last_exit_bar = -100

    def _bb(self, values: list, period: int, num_std: float):
        if len(values) < period:
            return None, None, None, None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        upper = mean + num_std * std
        lower = mean - num_std * std
        bw = (upper - lower) / mean if mean > 0 else 0.0
        return mean, upper, lower, bw

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

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.squeeze_lookback + self.bb_period + 5)
        volumes = ctx.volumes(30)
        if len(closes) < self.squeeze_lookback + self.bb_period or len(volumes) < 20:
            return None

        # Filter out extreme crisis regimes to prevent erratic fills
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        current_close = ctx.bar.close
        sma, upper, lower, current_bw = self._bb(closes, self.bb_period, self.bb_std)
        if sma is None or upper is None or lower is None or current_bw is None:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Position exit management - widened to let profitable trends develop without churn
        if ctx.has_position():
            pos_dir = ctx.position_direction()

            if pos_dir == "long":
                if current_close < sma and rsi < 42.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_momentum_breakdown_below_midband",
                            "price": current_close,
                            "sma": round(sma, 2),
                            "rsi": round(rsi, 2),
                        }
                    )
            elif pos_dir == "short":
                if current_close > sma and rsi > 58.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_momentum_reversal_above_midband",
                            "price": current_close,
                            "sma": round(sma, 2),
                            "rsi": round(rsi, 2),
                        }
                    )
            return None

        # Multi-bar hard cooldown after last exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Compute rolling bandwidth history across the lookback window
        bandwidth_history = []
        for i in range(len(closes) - self.squeeze_lookback, len(closes)):
            sub_closes = closes[: i + 1]
            _, _, _, bw = self._bb(sub_closes, self.bb_period, self.bb_std)
            if bw is not None:
                bandwidth_history.append(bw)

        if len(bandwidth_history) < self.squeeze_lookback:
            return None

        sorted_bw = sorted(bandwidth_history)
        threshold_idx = int(len(sorted_bw) * self.squeeze_quantile)
        squeeze_threshold = sorted_bw[threshold_idx]

        # Squeeze confirmation: prior bar was squeezed, and current bar is expanding
        prev_closes = closes[:-1]
        _, prev_upper, prev_lower, prev_bw = self._bb(prev_closes, self.bb_period, self.bb_std)
        if prev_bw is None or prev_upper is None or prev_lower is None:
            return None

        was_squeezed = prev_bw <= squeeze_threshold
        is_expanding = current_bw > prev_bw

        if not (was_squeezed and is_expanding):
            return None

        # Volume confirmation
        vol_sma = self._sma(volumes, 20)
        current_vol = ctx.bar.volume
        vol_confirmed = (vol_sma is not None) and (current_vol >= vol_sma * 1.05)
        if not vol_confirmed:
            return None

        # Long breakout: clean transition above upper band with solid bullish momentum
        if current_close > upper and closes[-2] <= prev_upper and rsi >= 56.0:
            confidence = min(0.85, 0.60 + (rsi - 50.0) / 100.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bollinger_squeeze_bullish_expansion",
                    "price": current_close,
                    "upper_band": round(upper, 2),
                    "bandwidth": round(current_bw, 5),
                    "prev_bandwidth": round(prev_bw, 5),
                    "squeeze_threshold": round(squeeze_threshold, 5),
                    "rsi": round(rsi, 2),
                    "volume_ratio": round(current_vol / vol_sma, 2) if vol_sma else 1.0,
                }
            )

        # Short breakdown: clean transition below lower band with solid bearish momentum
        if current_close < lower and closes[-2] >= prev_lower and rsi <= 44.0:
            confidence = min(0.85, 0.60 + (50.0 - rsi) / 100.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bollinger_squeeze_bearish_expansion",
                    "price": current_close,
                    "lower_band": round(lower, 2),
                    "bandwidth": round(current_bw, 5),
                    "prev_bandwidth": round(prev_bw, 5),
                    "squeeze_threshold": round(squeeze_threshold, 5),
                    "rsi": round(rsi, 2),
                    "volume_ratio": round(current_vol / vol_sma, 2) if vol_sma else 1.0,
                }
            )

        return None