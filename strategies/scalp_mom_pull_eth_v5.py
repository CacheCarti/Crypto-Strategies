from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class EthScalpPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthScalpPullbackAdvanced",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 190.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 9
        self.slow_period = 40
        self.rsi_period = 14
        self.vol_period = 20
        self.cooldown_bars = 50
        self.last_trade_bar = -999

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Filter out extreme volatility / crisis regimes
        if ctx.regime == "crisis" or ctx.market.get("crisis_score", 0.0) > 0.6:
            return None

        closes = ctx.closes(self.slow_period + 15)
        highs = ctx.highs(self.slow_period + 15)
        lows = ctx.lows(self.slow_period + 15)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.slow_period + 10 or len(volumes) < self.vol_period:
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)
        ema_slow_prev = self._ema(closes[:-5], self.slow_period)
        rsi_val = self._rsi(closes, self.rsi_period)
        vol_avg = sum(volumes[-self.vol_period:]) / self.vol_period

        if ema_fast is None or ema_slow is None or ema_slow_prev is None or rsi_val is None or vol_avg <= 0:
            return None

        current_close = closes[-1]
        current_low = lows[-1]
        current_high = highs[-1]
        prev_close = closes[-2]
        current_vol = volumes[-1]

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close < ema_slow or rsi_val > 75.0:
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "long_trend_break_or_overbought_exit",
                        "rsi": round(rsi_val, 2),
                        "close": current_close,
                        "ema_slow": round(ema_slow, 2)
                    })
            elif pos_dir == "short":
                if current_close > ema_slow or rsi_val < 25.0:
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "short_trend_break_or_oversold_exit",
                        "rsi": round(rsi_val, 2),
                        "close": current_close,
                        "ema_slow": round(ema_slow, 2)
                    })
            return None

        # Hard multi-bar cooldown gating to prevent overtrading
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Strict trend slope & separation
        trend_up = (ema_fast > ema_slow * 1.001) and (ema_slow > ema_slow_prev) and (current_close > ema_slow)
        trend_down = (ema_fast < ema_slow * 0.999) and (ema_slow < ema_slow_prev) and (current_close < ema_slow)
        vol_surge = current_vol >= vol_avg * 1.25

        # Long pullback: Trend is cleanly up, dipped below fast EMA and sharply recovered above it
        long_pullback_trigger = (
            trend_up
            and current_low <= ema_fast
            and current_close > ema_fast
            and prev_close < ema_fast * 1.001
            and 44.0 <= rsi_val <= 58.0
            and vol_surge
        )

        if long_pullback_trigger:
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.9, 0.70 + (0.15 if current_vol > vol_avg * 1.5 else 0.05))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "uptrend_ema_pullback_rejection",
                    "rsi": round(rsi_val, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "close": current_close,
                    "vol_ratio": round(current_vol / vol_avg, 2)
                }
            )

        # Short pullback: Trend is cleanly down, spiked above fast EMA and rejected back below it
        short_pullback_trigger = (
            trend_down
            and current_high >= ema_fast
            and current_close < ema_fast
            and prev_close > ema_fast * 0.999
            and 42.0 <= rsi_val <= 56.0
            and vol_surge
        )

        if short_pullback_trigger:
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.9, 0.70 + (0.15 if current_vol > vol_avg * 1.5 else 0.05))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "downtrend_ema_pullback_rejection",
                    "rsi": round(rsi_val, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "close": current_close,
                    "vol_ratio": round(current_vol / vol_avg, 2)
                }
            )

        return None