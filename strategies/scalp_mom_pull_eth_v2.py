from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class TrendPullbackScalp(Strategy):
    METADATA = {
        "name": "ETH Scalp Trend Pullback",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 200.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 9
        self.trend_period = 21
        self.baseline_period = 55
        self.cooldown_bars = 48
        self.last_exit_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Filter out extreme crisis regimes to prevent erratic chop
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        closes = ctx.closes(60)
        highs = ctx.highs(60)
        lows = ctx.lows(60)
        opens = ctx.opens(60)
        volumes = ctx.volumes(30)

        if len(closes) < 60:
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_trend = self._ema(closes, self.trend_period)
        ema_base = self._ema(closes, self.baseline_period)
        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)

        if ema_fast is None or ema_trend is None or ema_base is None or rsi is None or atr is None:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        current_high = highs[-1]
        current_low = lows[-1]

        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        avg_vol = sum(volumes[-20:]) / 20.0 if len(volumes) >= 20 else 1.0
        curr_vol = volumes[-1] if len(volumes) > 0 else 1.0

        # Position Management
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                # Swing exit or trend breakdown
                swing_high = max(highs[-12:-1])
                if current_high >= swing_high or current_close < ema_trend:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "scalp_exit_swing_high_or_trend_break",
                            "close": current_close,
                            "swing_high": swing_high,
                            "ema_trend": ema_trend,
                        },
                    )
            elif direction == "short":
                # Swing exit or trend breakdown
                swing_low = min(lows[-12:-1])
                if current_low <= swing_low or current_close > ema_trend:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "scalp_exit_swing_low_or_trend_break",
                            "close": current_close,
                            "swing_low": swing_low,
                            "ema_trend": ema_trend,
                        },
                    )
            return None

        # Hard multi-bar cooldown after exit to eliminate overtrading and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        candle_body = abs(current_close - current_open)
        has_momentum_volume = curr_vol >= 1.05 * avg_vol
        valid_body_size = candle_body >= (0.35 * atr)

        # LONG SETUP:
        # 1. Clear macro and intermediate uptrend alignment (EMA9 > EMA21 > EMA55)
        # 2. Strict pullback condition: Prior bar dipped below EMA9 or current low dipped below EMA9
        # 3. Confirmation: Current bar strongly bounces and closes back above EMA9 with a green candle
        # 4. RSI momentum gate (45 - 65) + volume confirmation
        bullish_alignment = ema_fast > ema_trend > ema_base
        long_pullback_dip = (prev_low <= ema_fast or current_low <= ema_fast) and prev_close <= (ema_fast * 1.001)
        long_bounce = current_close > ema_fast and current_close > current_open

        if bullish_alignment and long_pullback_dip and long_bounce and valid_body_size:
            if 45.0 <= rsi <= 64.0 and has_momentum_volume:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.85,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "strict_bullish_pullback_ema9_reclaim",
                        "close": current_close,
                        "ema9": ema_fast,
                        "ema21": ema_trend,
                        "ema55": ema_base,
                        "rsi": rsi,
                        "vol_ratio": curr_vol / avg_vol if avg_vol > 0 else 1.0,
                    },
                )

        # SHORT SETUP:
        # 1. Clear macro and intermediate downtrend alignment (EMA9 < EMA21 < EMA55)
        # 2. Strict pullback condition: Prior bar popped above EMA9 or current high poked above EMA9
        # 3. Confirmation: Current bar strongly rejects and closes back below EMA9 with a red candle
        # 4. RSI momentum gate (36 - 55) + volume confirmation
        bearish_alignment = ema_fast < ema_trend < ema_base
        short_pullback_poke = (prev_high >= ema_fast or current_high >= ema_fast) and prev_close >= (ema_fast * 0.999)
        short_rejection = current_close < ema_fast and current_close < current_open

        if bearish_alignment and short_pullback_poke and short_rejection and valid_body_size:
            if 36.0 <= rsi <= 55.0 and has_momentum_volume:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.85,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "strict_bearish_pullback_ema9_rejection",
                        "close": current_close,
                        "ema9": ema_fast,
                        "ema21": ema_trend,
                        "ema55": ema_base,
                        "rsi": rsi,
                        "vol_ratio": curr_vol / avg_vol if avg_vol > 0 else 1.0,
                    },
                )

        return None