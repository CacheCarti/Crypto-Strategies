from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdvanced",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(65)
        lows = ctx.lows(65)
        highs = ctx.highs(65)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema20 is None or ema50 is None or rsi is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        current_low = lows[-1]
        current_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Handle active positions and exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close < ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_ema20_break", "price": current_close, "ema20": ema20, "rsi": rsi}
                )
            elif pos_dir == "short" and current_close > ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_ema20_break", "price": current_close, "ema20": ema20, "rsi": rsi}
                )
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: reject trading in chaotic meltdown or crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long pullback setup:
        # 1. Structural uptrend: EMA20 comfortably above EMA50, current close above EMA50
        # 2. Pullback trigger: touched/dipped below EMA20 within last 2 bars, closed back above EMA20
        # 3. Quality filter: RSI between 42 and 58 to ensure healthy pullback rather than exhaustion/collapse
        long_trend = ema20 > ema50 and current_close > ema50
        long_pullback_touch = (prev_low <= ema20 or current_low <= ema20)
        long_reclaim = current_close > ema20 and prev_close <= ema20
        long_rsi_ok = 42.0 <= rsi <= 58.0

        if long_trend and long_pullback_touch and long_reclaim and long_rsi_ok:
            confidence = 0.75 if (ema20 - ema50) / ema50 > 0.005 else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_bull",
                    "price": current_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "rsi": rsi
                }
            )

        # Short pullback setup:
        # 1. Structural downtrend: EMA20 below EMA50, current close below EMA50
        # 2. Pullback trigger: touched/tested above EMA20 within last 2 bars, closed back below EMA20
        # 3. Quality filter: RSI between 42 and 58
        short_trend = ema20 < ema50 and current_close < ema50
        short_pullback_touch = (prev_high >= ema20 or current_high >= ema20)
        short_reclaim = current_close < ema20 and prev_close >= ema20
        short_rsi_ok = 42.0 <= rsi <= 58.0

        if short_trend and short_pullback_touch and short_reclaim and short_rsi_ok:
            confidence = 0.75 if (ema50 - ema20) / ema50 > 0.005 else 0.65
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_bear",
                    "price": current_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "rsi": rsi
                }
            )

        return None