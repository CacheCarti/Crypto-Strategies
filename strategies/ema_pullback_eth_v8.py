from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TrendPullbackEMA(Strategy):
    METADATA = {
        "name": "ETH Trend Pullback EMA",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 55
        self.rsi_period = 14
        self.vol_period = 20
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.last_entry_bar = -999

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = max(self.slow_period + 10, self.rsi_period + 10)
        closes = ctx.closes(needed)
        opens = ctx.opens(needed)
        highs = ctx.highs(needed)
        lows = ctx.lows(needed)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < needed:
            return None

        # Market regime filter: avoid choppy crisis/meltdown environments
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        curr_close = closes[-1]
        curr_open = opens[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        ema_fast = self._ema(closes, self.fast_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)
        ema_slow_prev = self._ema(closes[:-1], self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_fast_prev is None or ema_slow is None or ema_slow_prev is None or rsi is None:
            return None

        avg_vol = sum(volumes[-self.vol_period:]) / self.vol_period if len(volumes) >= self.vol_period else 0.0
        curr_vol = ctx.bar.volume
        vol_confirmed = avg_vol > 0 and curr_vol >= 0.85 * avg_vol

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            
            # Long exit: structural breakdown below trend line (EMA slow) or extreme overbought
            if direction == "long":
                if curr_close < ema_slow or rsi > 78.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_trend_break_or_overbought",
                            "close": round(curr_close, 2),
                            "ema_slow": round(ema_slow, 2),
                            "rsi": round(rsi, 2),
                        }
                    )
            # Short exit: structural break above trend line (EMA slow) or extreme oversold
            elif direction == "short":
                if curr_close > ema_slow or rsi < 22.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_trend_break_or_oversold",
                            "close": round(curr_close, 2),
                            "ema_slow": round(ema_slow, 2),
                            "rsi": round(rsi, 2),
                        }
                    )
            return None

        # Enforce strict post-exit cooldown
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_exit < self.cooldown_bars:
            return None

        # Long Setup:
        # 1. Macro Trend: Fast EMA well above Slow EMA and Slow EMA sloping upward
        # 2. Pullback & Reclaim: Previous bar dipped below Fast EMA, current bar closed back above Fast EMA
        # 3. Momentum & Volume confirmation: RSI in healthy recovery band (42 - 60) + volume confirmation
        long_trend = (ema_fast > ema_slow) and (ema_slow >= ema_slow_prev) and (curr_close > ema_slow)
        long_pullback = (prev_low <= ema_fast_prev or prev_close <= ema_fast_prev) and (curr_close > ema_fast)
        long_candle = curr_close > curr_open
        long_momentum = 42.0 <= rsi <= 60.0

        if long_trend and long_pullback and long_candle and long_momentum and vol_confirmed:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.78,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_trend_pullback_reclaim",
                    "close": round(curr_close, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "rsi": round(rsi, 2),
                    "vol_ratio": round(curr_vol / avg_vol, 2) if avg_vol > 0 else 1.0,
                }
            )

        # Short Setup:
        # 1. Macro Trend: Fast EMA below Slow EMA and Slow EMA sloping downward
        # 2. Pullback & Rejection: Previous bar touched above Fast EMA, current bar closed back below Fast EMA
        # 3. Momentum & Volume confirmation: RSI in healthy breakdown band (40 - 58) + volume confirmation
        short_trend = (ema_fast < ema_slow) and (ema_slow <= ema_slow_prev) and (curr_close < ema_slow)
        short_pullback = (prev_high >= ema_fast_prev or prev_close >= ema_fast_prev) and (curr_close < ema_fast)
        short_candle = curr_close < curr_open
        short_momentum = 40.0 <= rsi <= 58.0

        if short_trend and short_pullback and short_candle and short_momentum and vol_confirmed:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.78,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_trend_pullback_rejection",
                    "close": round(curr_close, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "rsi": round(rsi, 2),
                    "vol_ratio": round(curr_vol / avg_vol, 2) if avg_vol > 0 else 1.0,
                }
            )

        return None