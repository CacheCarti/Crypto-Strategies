from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,  # ~4 hours expected target hold
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -100
        self.spread_threshold = 2.0  # 2.0% spread trigger
        self.reversion_threshold = 0.5  # Mean reversion exit when spread compresses
        self.entry_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        
        if len(closes) < 30:
            return None

        # Feature readings
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Volatility & trend context for dynamic risk sizing
        atr = self._atr(highs, lows, closes, 14)
        atr_val = atr if atr is not None else (closes[-1] * 0.015)
        atr_bps = (atr_val / closes[-1]) * 10000.0

        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        # Structural Volatility Stops (Dynamic SL/TP matched to local ATR)
        dynamic_sl_bps = max(200.0, min(420.0, atr_bps * 1.5))
        dynamic_tp_bps = max(380.0, min(800.0, atr_bps * 2.8))

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_in_trade = ctx.bar_index - self.entry_bar

            # 1. Structural mean-reversion exit on spread compression
            if pos_dir == "long" and spread <= self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "bars_in_trade": bars_in_trade}
                )
            if pos_dir == "short" and spread >= -self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "bars_in_trade": bars_in_trade}
                )

            # 2. Timed structural exit to prevent drift and decay
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_decay_exit", "spread": spread, "bars_in_trade": bars_in_trade}
                )

            return None

        # Mandatory Cooldown Guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: skip crisis or extreme meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        current_close = closes[-1]

        # Entry Long: ETH outperforming BTC by > +2.0%, confirmed by local price > EMA20
        if spread >= self.spread_threshold and current_close > ema20:
            confidence = min(0.90, 0.60 + min(abs(spread) / 10.0, 0.25))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_momentum_outperformance",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "atr_bps": atr_bps,
                    "close": current_close,
                    "ema20": ema20,
                }
            )

        # Entry Short: ETH underperforming BTC by < -2.0%, confirmed by local price < EMA20
        if spread <= -self.spread_threshold and current_close < ema20:
            confidence = min(0.90, 0.60 + min(abs(spread) / 10.0, 0.25))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lagging_underperformance",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "atr_bps": atr_bps,
                    "close": current_close,
                    "ema20": ema20,
                }
            )

        return None