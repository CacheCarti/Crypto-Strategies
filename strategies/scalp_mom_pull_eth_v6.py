from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ScalpPullbackTrend(Strategy):
    METADATA = {
        "name": "Scalp Pullback Trend ETH",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 190.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 12
        self.trend_period = 48
        self.cooldown_bars = 75
        self.last_entry_bar = -999
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_period + 15)
        highs = ctx.highs(self.trend_period + 15)
        lows = ctx.lows(self.trend_period + 15)
        opens = ctx.opens(self.trend_period + 15)

        if len(closes) < self.trend_period + 10:
            return None

        # Filter extreme crisis regimes
        if ctx.regime == "crisis" or ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit"})
            return None

        fast_ema = self._ema(closes, self.fast_period)
        trend_ema = self._ema(closes, self.trend_period)
        trend_ema_prev = self._ema(closes[:-5], self.trend_period)
        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)

        if fast_ema is None or trend_ema is None or trend_ema_prev is None or rsi is None or atr is None:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        current_high = highs[-1]
        current_low = lows[-1]
        prev_close = closes[-2]
        bar_range = max(current_high - current_low, 1e-6)

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close < trend_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "trend_break_exit_long",
                            "close": current_close,
                            "trend_ema": trend_ema,
                            "rsi": rsi,
                        }
                    )
            elif pos_dir == "short":
                if current_close > trend_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "trend_break_exit_short",
                            "close": current_close,
                            "trend_ema": trend_ema,
                            "rsi": rsi,
                        }
                    )
            return None

        # Hard Cooldown Guard after entry or exit
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_entry < self.cooldown_bars or bars_since_exit < self.cooldown_bars:
            return None

        # Strong Trend Conditions (Slope & Fast/Slow Alignment)
        is_strong_bull = (
            fast_ema > trend_ema
            and trend_ema > trend_ema_prev
            and current_close > trend_ema
        )
        is_strong_bear = (
            fast_ema < trend_ema
            and trend_ema < trend_ema_prev
            and current_close < trend_ema
        )

        # Bullish Pullback: Price dips into fast EMA and rejects with a strong bounce candle
        bullish_candle_rejection = (current_close - current_low) / bar_range >= 0.55
        long_setup = (
            is_strong_bull
            and prev_close >= fast_ema
            and current_low <= fast_ema
            and current_close > fast_ema
            and current_close > current_open
            and bullish_candle_rejection
            and (45.0 <= rsi <= 65.0)
        )

        if long_setup:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "high_quality_pullback_ema_bounce_long",
                    "fast_ema": fast_ema,
                    "trend_ema": trend_ema,
                    "close": current_close,
                    "low": current_low,
                    "rsi": rsi,
                    "atr": atr,
                }
            )

        # Bearish Pullback: Price pushes up to fast EMA and rejects with a bearish rejection candle
        bearish_candle_rejection = (current_high - current_close) / bar_range >= 0.55
        short_setup = (
            is_strong_bear
            and prev_close <= fast_ema
            and current_high >= fast_ema
            and current_close < fast_ema
            and current_close < current_open
            and bearish_candle_rejection
            and (35.0 <= rsi <= 55.0)
        )

        if short_setup:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "high_quality_pullback_ema_rejection_short",
                    "fast_ema": fast_ema,
                    "trend_ema": trend_ema,
                    "close": current_close,
                    "high": current_high,
                    "rsi": rsi,
                    "atr": atr,
                }
            )

        return None