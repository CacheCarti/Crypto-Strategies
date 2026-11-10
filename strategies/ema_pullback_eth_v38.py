from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackConfirmed(Strategy):
    METADATA = {
        "name": "EthEmaPullbackConfirmed",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        closes = ctx.closes(65)
        if len(closes) < 60:
            return None

        highs = ctx.highs(65)
        lows = ctx.lows(65)

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20 is None or ema50 is None or ema20_prev is None or ema50_prev is None:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        c_now = closes[-1]
        c_prev = closes[-2]
        l_prev = lows[-2]
        h_prev = highs[-2]

        pos_dir = ctx.position_direction()

        # Exit conditions: EMA20 cross against position
        if pos_dir == "long" and c_now < ema20:
            return ctx.signal(
                "flat",
                confidence=0.65,
                metadata={
                    "reason": "long_exit_cross_below_ema20",
                    "close": c_now,
                    "ema20": ema20,
                    "bar_index": ctx.bar_index
                }
            )

        if pos_dir == "short" and c_now > ema20:
            return ctx.signal(
                "flat",
                confidence=0.65,
                metadata={
                    "reason": "short_exit_cross_above_ema20",
                    "close": c_now,
                    "ema20": ema20,
                    "bar_index": ctx.bar_index
                }
            )

        # Do not enter if already positioned or in post-exit cooldown
        if ctx.has_position():
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Cross-asset alignment filter using BTC 24h momentum
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Volatility condition: prevent entries in completely compressed or blown-out ATR states
        atr_pct = (atr / c_now) * 10000.0
        if atr_pct < 25.0 or atr_pct > 350.0:
            return None

        # Long Setup:
        # 1. Structural Uptrend: EMA20 > EMA50 and Price > EMA50
        # 2. Pullback & Reclaim: Previous candle tested/dipped below EMA20, Current candle closes firmly back above EMA20
        # 3. Cross-asset confirmation: BTC 24h momentum non-negative
        long_trend = (c_now > ema50) and (ema20 > ema50)
        long_pullback_reclaim = (l_prev <= ema20_prev) and (c_prev <= ema20_prev * 1.002) and (c_now > ema20)
        long_btc_confirm = btc_ret >= -0.5

        if long_trend and long_pullback_reclaim and long_btc_confirm:
            conf = min(0.85, 0.60 + max(0.0, btc_ret * 0.03))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_long_confirmed",
                    "close": c_now,
                    "ema20": ema20,
                    "ema50": ema50,
                    "btc_return_pct": btc_ret,
                    "atr_bps": atr_pct
                }
            )

        # Short Setup:
        # 1. Structural Downtrend: EMA20 < EMA50 and Price < EMA50
        # 2. Pullback & Rejection: Previous candle tested/spiked above EMA20, Current candle closes back below EMA20
        # 3. Cross-asset confirmation: BTC 24h momentum non-positive
        short_trend = (c_now < ema50) and (ema20 < ema50)
        short_pullback_reject = (h_prev >= ema20_prev) and (c_prev >= ema20_prev * 0.998) and (c_now < ema20)
        short_btc_confirm = btc_ret <= 0.5

        if short_trend and short_pullback_reject and short_btc_confirm:
            conf = min(0.85, 0.60 + max(0.0, -btc_ret * 0.03))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reject_short_confirmed",
                    "close": c_now,
                    "ema20": ema20,
                    "ema50": ema50,
                    "btc_return_pct": btc_ret,
                    "atr_bps": atr_pct
                }
            )

        return None