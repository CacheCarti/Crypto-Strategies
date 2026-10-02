from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ScalpPullbackContinuation(Strategy):
    METADATA = {
        "name": "ScalpPullbackContinuation",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 210.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 12
        self.slow_period = 48
        self.rsi_period = 14
        self.vol_period = 16
        self.cooldown_bars = 36
        self.last_action_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.last_action_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 20)
        highs = ctx.highs(self.slow_period + 20)
        lows = ctx.lows(self.slow_period + 20)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.slow_period + 15 or len(volumes) < self.vol_period:
            return None

        # Position exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            recent_highs = highs[-6:-1]
            recent_lows = lows[-6:-1]

            if pos_dir == "long" and recent_highs:
                swing_target = max(recent_highs)
                if ctx.bar.close >= swing_target:
                    self.last_action_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "swing_high_target_hit",
                            "price": ctx.bar.close,
                            "swing_target": swing_target,
                        }
                    )
            elif pos_dir == "short" and recent_lows:
                swing_target = min(recent_lows)
                if ctx.bar.close <= swing_target:
                    self.last_action_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "swing_low_target_hit",
                            "price": ctx.bar.close,
                            "swing_target": swing_target,
                        }
                    )
            return None

        # Hard cooldown enforcement
        if ctx.bar_index - self.last_action_bar < self.cooldown_bars:
            return None

        # Market regime filter
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")

        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)
        avg_vol = self._sma(volumes, self.vol_period)

        if fast_ema is None or slow_ema is None or rsi is None or avg_vol is None:
            return None

        curr_close = ctx.bar.close
        curr_open = ctx.bar.open
        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_vol = ctx.bar.volume

        prev_high = highs[-2]
        prev_low = lows[-2]

        # Require significant EMA separation and volume support
        ema_spread_bps = ((fast_ema - slow_ema) / slow_ema) * 10000.0
        vol_confirmed = curr_vol >= avg_vol * 1.10

        # Long Setup: Macro Bull Trend + Pullback completed + Breakout bar with Volume
        strong_uptrend = trend_regime == "bull" and ema_spread_bps > 15.0 and curr_close > slow_ema
        pullback_recovered = prev_low <= fast_ema and curr_close > fast_ema and curr_close > prev_high
        bullish_candle = curr_close > curr_open and (curr_close - curr_low) > 0.6 * (curr_high - curr_low + 1e-8)
        rsi_valid_long = 42.0 <= rsi <= 58.0

        if strong_uptrend and pullback_recovered and bullish_candle and vol_confirmed and rsi_valid_long:
            self.last_action_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.82,
                stop_loss_bps=95.0,
                take_profit_bps=210.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "pullback_bounce_breakout_uptrend",
                    "fast_ema": round(fast_ema, 2),
                    "slow_ema": round(slow_ema, 2),
                    "ema_spread_bps": round(ema_spread_bps, 1),
                    "rsi": round(rsi, 2),
                    "price": curr_close,
                }
            )

        # Short Setup: Macro Bear Trend + Pullback completed + Breakdown bar with Volume
        strong_downtrend = trend_regime == "bear" and ema_spread_bps < -15.0 and curr_close < slow_ema
        pullback_rejected = prev_high >= fast_ema and curr_close < fast_ema and curr_close < prev_low
        bearish_candle = curr_close < curr_open and (curr_high - curr_close) > 0.6 * (curr_high - curr_low + 1e-8)
        rsi_valid_short = 42.0 <= rsi <= 58.0

        if strong_downtrend and pullback_rejected and bearish_candle and vol_confirmed and rsi_valid_short:
            self.last_action_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.82,
                stop_loss_bps=95.0,
                take_profit_bps=210.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "pullback_rejection_breakdown_downtrend",
                    "fast_ema": round(fast_ema, 2),
                    "slow_ema": round(slow_ema, 2),
                    "ema_spread_bps": round(ema_spread_bps, 1),
                    "rsi": round(rsi, 2),
                    "price": curr_close,
                }
            )

        return None