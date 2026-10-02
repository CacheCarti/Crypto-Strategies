from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class SolBollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "SolBollingerSqueezeBreakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 120,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.2
        self.squeeze_lookback = 100
        self.squeeze_quantile_pct = 15.0  # Top 15% tightest bandwidths (strict squeeze)
        self.cooldown_bars = 14            # Hard multi-bar cooldown after exit
        self.last_exit_bar = -999
        self.bars_since_squeeze_max = 3

    def _bollinger(self, closes: List[float], period: int, num_std: float):
        if len(closes) < period:
            return None, None, None, None
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
        # Avoid turbulent crisis periods
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit"})
            return None

        closes = ctx.closes(self.squeeze_lookback + self.bb_period + 5)
        volumes = ctx.volumes(20)
        if len(closes) < self.squeeze_lookback + self.bb_period or len(volumes) < 20:
            return None

        mid, upper, lower, current_bw = self._bollinger(closes, self.bb_period, self.bb_std)
        if mid is None or current_bw is None:
            return None

        current_price = closes[-1]
        prev_price = closes[-2]
        rsi_val = self._rsi(closes, 14) or 50.0

        # Position Management & Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            
            if direction == "long":
                # Exit when momentum fully reverses below mid-band with weak RSI or extreme overbought exhaustion
                if (current_price < mid and rsi_val < 48.0) or rsi_val > 80.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.80,
                        metadata={
                            "reason": "long_momentum_exhaustion_or_mid_loss",
                            "close": current_price,
                            "mid": mid,
                            "rsi": rsi_val,
                        }
                    )
            elif direction == "short":
                # Exit when price recovers above mid-band with strong RSI or extreme oversold bounce
                if (current_price > mid and rsi_val > 52.0) or rsi_val < 20.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.80,
                        metadata={
                            "reason": "short_momentum_exhaustion_or_mid_recovery",
                            "close": current_price,
                            "mid": mid,
                            "rsi": rsi_val,
                        }
                    )
            return None

        # Hard cooldown check after closing a position
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Compute historical bandwidths for squeeze detection over lookback window
        bandwidths = []
        for i in range(len(closes) - self.squeeze_lookback, len(closes)):
            sub_closes = closes[:i + 1]
            _, _, _, bw = self._bollinger(sub_closes, self.bb_period, self.bb_std)
            if bw is not None:
                bandwidths.append(bw)

        if len(bandwidths) < self.squeeze_lookback:
            return None

        # Squeeze threshold: tight 15th percentile over lookback
        sorted_bw = sorted(bandwidths[:-1])
        pct_idx = int(len(sorted_bw) * (self.squeeze_quantile_pct / 100.0))
        squeeze_threshold = sorted_bw[min(pct_idx, len(sorted_bw) - 1)]

        # Squeeze must have occurred within the last few bars
        recent_bandwidths = bandwidths[-self.bars_since_squeeze_max:]
        had_recent_squeeze = any(bw <= squeeze_threshold for bw in recent_bandwidths)
        if not had_recent_squeeze:
            return None

        # Volume expansion filter to ensure genuine breakout
        avg_vol = sum(volumes[-20:]) / 20.0
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        if vol_ratio < 1.05:
            return None

        # Long breakout: close breaks above upper band on strong RSI momentum
        if current_price > upper and prev_price <= upper and rsi_val >= 56.0:
            confidence = min(0.90, max(0.65, 0.65 + (rsi_val - 50.0) / 100.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bb_tight_squeeze_expansion_breakout_long",
                    "close": current_price,
                    "upper_band": upper,
                    "bandwidth": current_bw,
                    "squeeze_threshold": squeeze_threshold,
                    "rsi": rsi_val,
                    "vol_ratio": vol_ratio,
                }
            )

        # Short breakout: close breaks below lower band on downward RSI momentum
        if current_price < lower and prev_price >= lower and rsi_val <= 44.0:
            confidence = min(0.90, max(0.65, 0.65 + (50.0 - rsi_val) / 100.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bb_tight_squeeze_expansion_breakout_short",
                    "close": current_price,
                    "lower_band": lower,
                    "bandwidth": current_bw,
                    "squeeze_threshold": squeeze_threshold,
                    "rsi": rsi_val,
                    "vol_ratio": vol_ratio,
                }
            )

        return None