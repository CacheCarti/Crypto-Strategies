from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdvanced",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
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
        closes = ctx.closes(70)
        lows = ctx.lows(70)
        highs = ctx.highs(70)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, 14)

        if ema20 is None or ema50 is None or rsi is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]

        # Handle Exits for open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_ema20_cross_under", "close": curr_close, "ema20": ema20}
                )
            elif pos_dir == "short" and curr_close > ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_ema20_cross_over", "close": curr_close, "ema20": ema20}
                )
            return None

        # Post-exit cooldown gate to prevent churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries in extreme crisis or meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup:
        # 1. Structural Uptrend: EMA20 comfortably above EMA50, and current price above EMA50
        # 2. Reclaimed Pullback: Low touched or penetrated EMA20, but candle closed back above it firmly
        # 3. Prior bar or current candle dipped (prev_close <= ema20 or curr_low <= ema20)
        # 4. Momentum Quality: RSI pullback filter (40 <= RSI <= 62) to reject exhausted extensions
        long_trend = (ema20 > ema50) and (curr_close > ema50)
        long_pullback_touch = (curr_low <= ema20) or (prev_close <= ema20)
        long_reclaim = (curr_close > ema20) and (curr_close > prev_close)
        long_rsi_ok = 40.0 <= rsi <= 62.0

        if long_trend and long_pullback_touch and long_reclaim and long_rsi_ok:
            conf = min(0.90, max(0.60, 0.65 + (ema20 - ema50) / ema50 * 5.0))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_long",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "rsi": round(rsi, 2),
                    "regime": regime
                }
            )

        # Short Setup:
        # Mirror of Long setup with RSI bounded between 38 and 60
        short_trend = (ema20 < ema50) and (curr_close < ema50)
        short_pullback_touch = (curr_high >= ema20) or (prev_close >= ema20)
        short_reclaim = (curr_close < ema20) and (curr_close < prev_close)
        short_rsi_ok = 38.0 <= rsi <= 60.0

        if short_trend and short_pullback_touch and short_reclaim and short_rsi_ok:
            conf = min(0.90, max(0.60, 0.65 + (ema50 - ema20) / ema50 * 5.0))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_short",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "rsi": round(rsi, 2),
                    "regime": regime
                }
            )

        return None