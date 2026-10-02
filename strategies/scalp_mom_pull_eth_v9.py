from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ScalpPullbackContinuation(Strategy):
    METADATA = {
        "name": "ScalpPullbackContinuation",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 13
        self.slow_period = 48
        self.rsi_period = 14
        self.vol_period = 20
        self.cooldown_bars = 80
        self.last_trade_bar = -200

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 20)
        opens = ctx.opens(self.slow_period + 20)
        highs = ctx.highs(self.slow_period + 20)
        lows = ctx.lows(self.slow_period + 20)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.slow_period + 15 or len(volumes) < self.vol_period:
            return None

        # Hard multi-bar cooldown guard
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Regime protection: skip stressed or meltdown markets
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.4:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        current_low = lows[-1]
        current_high = highs[-1]

        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        prev_slow_ema = self._ema(closes[:-1], self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or prev_slow_ema is None or rsi is None:
            return None

        # Volume filter: ensure above-average volume confirms the continuation candle
        avg_vol = sum(volumes[-self.vol_period:]) / self.vol_period
        vol_surge = volumes[-1] >= (avg_vol * 1.10) if avg_vol > 0 else False

        # Strong trend separation requirement (>0.15% spread) and positive slope
        bull_trend = (
            fast_ema > slow_ema * 1.0015
            and slow_ema > prev_slow_ema
            and current_close > slow_ema
        )
        bear_trend = (
            fast_ema < slow_ema * 0.9985
            and slow_ema < prev_slow_ema
            and current_close < slow_ema
        )

        # Long Setup:
        # Prior bar dipped into/below Fast EMA (pullback), current bar shows bullish reversal closing above Fast EMA
        if bull_trend and not ctx.has_position():
            prior_pulled_back = prev_low <= fast_ema * 1.0005
            bullish_thrust = (
                current_close > fast_ema
                and current_close > current_open
                and current_close > prev_close
            )
            rsi_valid = 40.0 <= rsi <= 55.0

            if prior_pulled_back and bullish_thrust and rsi_valid and vol_surge:
                self.last_trade_bar = ctx.bar_index
                confidence = 0.80 if rsi <= 48.0 else 0.70
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "ema_pullback_continuation_long",
                        "rsi": round(rsi, 2),
                        "fast_ema": round(fast_ema, 2),
                        "slow_ema": round(slow_ema, 2),
                        "close": round(current_close, 2),
                        "vol_ratio": round(volumes[-1] / max(avg_vol, 1e-6), 2),
                    },
                )

        # Short Setup:
        # Prior bar rose into/above Fast EMA (pullback), current bar shows bearish rejection closing below Fast EMA
        if bear_trend and not ctx.has_position():
            prior_pulled_back = prev_high >= fast_ema * 0.9995
            bearish_thrust = (
                current_close < fast_ema
                and current_close < current_open
                and current_close < prev_close
            )
            rsi_valid = 45.0 <= rsi <= 60.0

            if prior_pulled_back and bearish_thrust and rsi_valid and vol_surge:
                self.last_trade_bar = ctx.bar_index
                confidence = 0.80 if rsi >= 52.0 else 0.70
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "ema_pullback_continuation_short",
                        "rsi": round(rsi, 2),
                        "fast_ema": round(fast_ema, 2),
                        "slow_ema": round(slow_ema, 2),
                        "close": round(current_close, 2),
                        "vol_ratio": round(volumes[-1] / max(avg_vol, 1e-6), 2),
                    },
                )

        return None