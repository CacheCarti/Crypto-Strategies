from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class CalmTrendMomentum(Strategy):
    METADATA = {
        "name": "CalmTrendMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_short_window = 20
        self.vol_long_window = 180
        self.ema_period = 24
        self.rsi_period = 14
        self.slope_lookback = 3
        self.slope_threshold_bps = 3.0
        self.cooldown_bars = 7
        self.last_exit_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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

    def _realized_vol(self, closes: list, window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        returns = []
        for i in range(len(closes) - window, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                return None
            returns.append((closes[i] - prev) / prev)
        mean_ret = sum(returns) / window
        var = sum((r - mean_ret) ** 2 for r in returns) / window
        return math.sqrt(var) * 10000.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.vol_long_window + self.vol_short_window + 5
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        current_close = closes[-1]

        # Calculate short-term realized volatility series over the long window
        vol_series = []
        for end_idx in range(len(closes) - self.vol_long_window + 1, len(closes) + 1):
            sub_closes = closes[:end_idx]
            v = self._realized_vol(sub_closes, self.vol_short_window)
            if v is not None:
                vol_series.append(v)

        if len(vol_series) < self.vol_long_window:
            return None

        current_vol = vol_series[-1]
        sorted_vols = sorted(vol_series)
        median_vol = sorted_vols[len(sorted_vols) // 2]

        # Market regime & volatility spike exit
        market_regime = ctx.market.get("regime", "NORMAL")
        is_vol_spike = current_vol > (median_vol * 1.65) or market_regime in ["CRISIS", "MELTDOWN"]

        if ctx.has_position():
            if is_vol_spike:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "volatility_spike_exit",
                        "current_vol": round(current_vol, 2),
                        "median_vol": round(median_vol, 2),
                        "market_regime": market_regime,
                        "close": current_close,
                    },
                )
            return None

        # Cooldown guard after position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter for calm market: short-term vol must be below median historical vol
        is_calm_tape = current_vol < (median_vol * 0.95)
        if not is_calm_tape or is_vol_spike:
            return None

        # Trend & Momentum calculation
        ema_now = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-self.slope_lookback], self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_now is None or ema_prev is None or rsi is None or ema_prev == 0:
            return None

        ema_slope_bps = ((ema_now - ema_prev) / (self.slope_lookback * ema_prev)) * 10000.0

        # Long Setup: Price above EMA, EMA sloping up, RSI confirming healthy momentum
        if current_close > ema_now and ema_slope_bps >= self.slope_threshold_bps and 50.0 < rsi < 68.0:
            confidence = min(0.9, 0.6 + (ema_slope_bps / 50.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_tape_bullish_trend_continuation",
                    "current_vol": round(current_vol, 2),
                    "median_vol": round(median_vol, 2),
                    "ema_slope_bps": round(ema_slope_bps, 2),
                    "rsi": round(rsi, 2),
                    "close": current_close,
                },
            )

        # Short Setup: Price below EMA, EMA sloping down, RSI confirming healthy bearish pressure
        if current_close < ema_now and ema_slope_bps <= -self.slope_threshold_bps and 32.0 < rsi < 50.0:
            confidence = min(0.9, 0.6 + (abs(ema_slope_bps) / 50.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_tape_bearish_trend_continuation",
                    "current_vol": round(current_vol, 2),
                    "median_vol": round(median_vol, 2),
                    "ema_slope_bps": round(ema_slope_bps, 2),
                    "rsi": round(rsi, 2),
                    "close": current_close,
                },
            )

        return None