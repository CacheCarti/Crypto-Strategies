from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class RegimeGatedMomentum(Strategy):
    METADATA = {
        "name": "RegimeGatedMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.median_period = 168
        self.sma_period = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _realized_vol(self, returns_slice):
        n = len(returns_slice)
        if n < 2:
            return 0.0
        mean = sum(returns_slice) / n
        var = sum((x - mean) ** 2 for x in returns_slice) / (n - 1)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        required_len = self.median_period + self.vol_period + 5
        closes = ctx.closes(required_len)
        if len(closes) < required_len:
            return None

        opens = ctx.opens(required_len)
        if len(opens) < required_len:
            return None

        # Calculate 1-bar returns
        returns = []
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append((closes[i] - prev) / prev)
            else:
                returns.append(0.0)

        if len(returns) < self.median_period + self.vol_period:
            return None

        # Calculate rolling 24-bar realized volatility across the 168-bar lookback window
        rolling_vols = []
        for i in range(len(returns) - self.median_period, len(returns) + 1):
            slice_ret = returns[i - self.vol_period:i]
            rolling_vols.append(self._realized_vol(slice_ret))

        if not rolling_vols:
            return None

        current_vol = rolling_vols[-1]
        historical_vols = rolling_vols[:-1] if len(rolling_vols) > 1 else [current_vol]
        median_vol = statistics.median(historical_vols)

        if median_vol <= 1e-8:
            vol_ratio = 1.0
        else:
            vol_ratio = current_vol / median_vol

        current_price = ctx.bar.close
        sma_curr = self._sma(closes, self.sma_period)
        sma_prev = self._sma(closes[:-1], self.sma_period)

        if sma_curr is None or sma_prev is None:
            return None

        prev_price = closes[-2]
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Regime Gating: Stricter threshold to avoid overtrading
        is_active_regime = vol_ratio >= 1.15
        is_quiet_regime = vol_ratio < 0.75

        # Position Management & Exits with hysteresis buffer to give trades breathing room
        if has_pos:
            if is_quiet_regime:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "quiet_regime_vol_decay",
                        "vol_ratio": round(vol_ratio, 4),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "price": current_price,
                    }
                )

            # Reversal exits with buffer (prevents noise chop)
            if pos_dir == "long" and current_price < sma_curr * 0.995:
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={
                        "reason": "long_momentum_breakdown",
                        "price": current_price,
                        "sma": round(sma_curr, 2),
                        "vol_ratio": round(vol_ratio, 4),
                    }
                )
            elif pos_dir == "short" and current_price > sma_curr * 1.005:
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={
                        "reason": "short_momentum_breakout",
                        "price": current_price,
                        "sma": round(sma_curr, 2),
                        "vol_ratio": round(vol_ratio, 4),
                    }
                )
            return None

        # Hard multi-bar cooldown after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Gate on active regime only
        if not is_active_regime:
            return None

        # Crisp entry triggers: strict transition crossovers with directional candle confirmation
        # Eliminates loose continuation branches that fire on every bar
        is_bull_candle = current_price > opens[-1]
        is_bear_candle = current_price < opens[-1]

        crossed_above = (prev_price <= sma_prev) and (current_price > sma_curr * 1.001) and is_bull_candle
        crossed_below = (prev_price >= sma_prev) and (current_price < sma_curr * 0.999) and is_bear_candle

        # Confidence scales with volatility expansion above median
        confidence = min(max(0.55 + (vol_ratio - 1.15) * 0.30, 0.55), 0.90)

        if crossed_above:
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "vol_gated_momentum_long_cross",
                    "vol_ratio": round(vol_ratio, 4),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "price": current_price,
                    "sma": round(sma_curr, 2),
                }
            )

        if crossed_below:
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "vol_gated_momentum_short_cross",
                    "vol_ratio": round(vol_ratio, 4),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "price": current_price,
                    "sma": round(sma_curr, 2),
                }
            )

        return None