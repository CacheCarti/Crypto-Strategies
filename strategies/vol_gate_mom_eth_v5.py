from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math
import statistics

class VolatilityGatedMomentum(Strategy):
    METADATA = {
        "name": "Volatility Gated Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 43200,  # ~12 hours swing hold
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_period = 20
        self.vol_history_period = 160
        self.ma_period = 28
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_trade_bar = -999

    def _stddev(self, values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _get_realized_vol(self, closes: List[float], period: int) -> float:
        if len(closes) < period + 1:
            return 0.0
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        return self._stddev(returns[-period:])

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.vol_history_period + self.rv_period + 2
        closes = ctx.closes(needed)
        if len(closes) < needed:
            return None

        current_price = ctx.bar.close
        current_bar = ctx.bar_index

        # Calculate current realized volatility (20 bars)
        current_rv = self._get_realized_vol(closes, self.rv_period)

        # Calculate historical distribution of realized volatility (last 160 bars)
        rv_series = []
        for offset in range(self.vol_history_period):
            end_idx = len(closes) - offset
            start_idx = end_idx - (self.rv_period + 1)
            window_closes = closes[start_idx:end_idx]
            rv = self._get_realized_vol(window_closes, self.rv_period)
            rv_series.append(rv)

        median_rv = statistics.median(rv_series) if rv_series else 0.0
        if median_rv <= 1e-8:
            return None

        vol_ratio = current_rv / median_rv
        ema_trend = self._ema(closes, self.ma_period)
        if ema_trend is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Regime exit: market went completely quiet or volatility collapsed
        if has_pos and vol_ratio < 0.80:
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "volatility_subsided_quiet_regime",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_rv": round(current_rv, 6),
                    "median_rv": round(median_rv, 6),
                    "price": current_price,
                }
            )

        # Reversal exit if price crosses adverse to current position
        if has_pos:
            if pos_dir == "long" and current_price < ema_trend:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "trend_loss_below_ema",
                        "price": current_price,
                        "ema": round(ema_trend, 2),
                    }
                )
            elif pos_dir == "short" and current_price > ema_trend:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "trend_loss_above_ema",
                        "price": current_price,
                        "ema": round(ema_trend, 2),
                    }
                )

        # Enforce cooldown after recent trades or exits
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None
        if current_bar - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Volatility must be elevated (> 10% above median)
        if vol_ratio >= 1.10:
            prev_close = closes[-2]
            prev_ema = self._ema(closes[:-1], self.ma_period)
            if prev_ema is None:
                return None

            # Scaled confidence based on volatility expansion
            confidence = min(0.90, max(0.40, 0.50 + 0.35 * (vol_ratio - 1.10)))

            # Bullish breakout/crossover into elevated volatility
            if not has_pos and prev_close <= prev_ema and current_price > ema_trend:
                self.last_trade_bar = current_bar
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_ma_cross_vol_expansion",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "ema": round(ema_trend, 2),
                        "price": current_price,
                    }
                )

            # Bearish breakdown/crossunder into elevated volatility
            if not has_pos and prev_close >= prev_ema and current_price < ema_trend:
                self.last_trade_bar = current_bar
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_ma_cross_vol_expansion",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "ema": round(ema_trend, 2),
                        "price": current_price,
                    }
                )

        return None