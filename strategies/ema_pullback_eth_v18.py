from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackConfirmation(Strategy):
    METADATA = {
        "name": "EthEmaPullbackConfirmation",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(65)
        highs = ctx.highs(65)
        lows = ctx.lows(65)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        if ema20 is None or ema50 is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / current_close) if (atr and current_close > 0) else 0.01

        # Position exit handling based on EMA20 cross against trade
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close < ema20:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_below_ema20", "close": current_close, "ema20": ema20}
                )
            elif pos_dir == "short" and current_close > ema20:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_above_ema20", "close": current_close, "ema20": ema20}
                )
            return None

        # Cross-asset trend confirmation filter
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Volatility expansion regime sanity check: filter out ultra low-vol drift or extreme crisis
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # LONG SETUP:
        # 1. Structural uptrend: EMA20 > EMA50 and Price > EMA50
        # 2. Pullback: Previous bar dipped to/below EMA20 (low <= EMA20)
        # 3. Reclaim: Current bar closes decisively back above EMA20
        # 4. Confirmation: BTC 24h return non-negative and ATR healthy (> 0.4%)
        if ema20 > ema50 and current_close > ema50:
            if prev_low <= ema20 and current_close > ema20 and current_close > prev_close:
                if btc_ret >= -0.5 and atr_pct >= 0.004:
                    self.last_trade_bar = ctx.bar_index
                    conf = min(0.85, 0.55 + max(0.0, btc_ret) * 0.03)
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "ema_pullback_reclaim_long",
                            "close": current_close,
                            "ema20": ema20,
                            "ema50": ema50,
                            "btc_ret": btc_ret,
                            "atr_pct": atr_pct
                        }
                    )

        # SHORT SETUP:
        # 1. Structural downtrend: EMA20 < EMA50 and Price < EMA50
        # 2. Pullback: Previous bar popped to/above EMA20 (high >= EMA20)
        # 3. Rejection: Current bar closes decisively back below EMA20
        # 4. Confirmation: BTC 24h return non-positive and ATR healthy (> 0.4%)
        if ema20 < ema50 and current_close < ema50:
            if prev_high >= ema20 and current_close < ema20 and current_close < prev_close:
                if btc_ret <= 0.5 and atr_pct >= 0.004:
                    self.last_trade_bar = ctx.bar_index
                    conf = min(0.85, 0.55 + max(0.0, -btc_ret) * 0.03)
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "ema_pullback_rejection_short",
                            "close": current_close,
                            "ema20": ema20,
                            "ema50": ema50,
                            "btc_ret": btc_ret,
                            "atr_pct": atr_pct
                        }
                    )

        return None