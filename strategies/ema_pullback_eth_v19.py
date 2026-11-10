from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdvanced",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        closes = ctx.closes(70)
        highs = ctx.highs(70)
        lows = ctx.lows(70)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Compute indicators
        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        if ema20 is None or ema50 is None:
            return None

        # Previous bar EMA values
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)
        if ema20_prev is None or ema50_prev is None:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        c_now = closes[-1]
        c_prev = closes[-2]
        l_prev = lows[-2]
        h_prev = highs[-2]

        current_dir = ctx.position_direction()

        # Handle active position management (Exits)
        if ctx.has_position():
            if current_dir == "long" and c_now < ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_long_ema20_break", "price": c_now, "ema20": ema20}
                )
            if current_dir == "short" and c_now > ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_short_ema20_break", "price": c_now, "ema20": ema20}
                )
            return None

        # Gating: Cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Macro/Regime filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Trend and Slope conviction check
        ema_spread = (ema20 - ema50) / ema50
        min_spread = 0.0035  # At least 35 bps spread to ensure strong trend structure

        # 1. LONG SETUP:
        # - Strong uptrend: EMA20 comfortably above EMA50, EMA50 trending up
        # - Setup: Previous bar dipped below or touched EMA20 (low <= EMA20)
        # - Confirmation: Current bar firmly reclaims EMA20 with a positive close
        long_trend = (ema20 > ema50) and (ema_spread > min_spread) and (ema50 >= ema50_prev)
        long_pullback = (l_prev <= ema20_prev) and (c_prev <= ema20_prev * 1.002)
        long_reclaim = (c_now > ema20) and (c_now > c_prev)

        if long_trend and long_pullback and long_reclaim:
            # Dynamic stop based on ATR
            sl_bps = max(160.0, min(300.0, (atr * 1.8 / c_now) * 10000.0))
            tp_bps = sl_bps * 2.0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "ema_pullback_reclaim_bull",
                    "price": c_now,
                    "ema20": ema20,
                    "ema50": ema50,
                    "spread_bps": ema_spread * 10000.0,
                    "atr": atr,
                }
            )

        # 2. SHORT SETUP:
        # - Strong downtrend: EMA20 comfortably below EMA50, EMA50 trending down
        # - Setup: Previous bar rallied to touch or poke above EMA20 (high >= EMA20)
        # - Confirmation: Current bar confirms rejection back below EMA20 with a lower close
        short_trend = (ema20 < ema50) and (ema_spread < -min_spread) and (ema50 <= ema50_prev)
        short_pullback = (h_prev >= ema20_prev) and (c_prev >= ema20_prev * 0.998)
        short_rejection = (c_now < ema20) and (c_now < c_prev)

        if short_trend and short_pullback and short_rejection:
            sl_bps = max(160.0, min(300.0, (atr * 1.8 / c_now) * 10000.0))
            tp_bps = sl_bps * 2.0
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "ema_pullback_reject_bear",
                    "price": c_now,
                    "ema20": ema20,
                    "ema50": ema50,
                    "spread_bps": ema_spread * 10000.0,
                    "atr": atr,
                }
            )

        return None