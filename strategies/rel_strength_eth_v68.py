from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthBreakout(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.prev_spread = 0.0
        self.spread_threshold = 2.2
        self.min_hold_bars = 4
        self.entry_bar = -1

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
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < 40 or len(highs) < 20 or len(lows) < 20:
            return None

        # Feature reads
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        current_spread = eth_ret - btc_ret

        # Moving average filter for trend confirmation
        ema20 = self._ema(closes, 20)
        close = ctx.bar.close

        # Volatility check to avoid trading during absolute dead markets or flash meltdowns
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or ema20 is None:
            self.prev_spread = current_spread
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            self.prev_spread = current_spread
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 5

            # Exit condition: spread mean-reverts past zero or momentum reverses
            if pos_dir == "long" and current_spread <= 0.0 and bars_held >= self.min_hold_bars:
                self.prev_spread = current_spread
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": current_spread, "bars_held": bars_held}
                )
            elif pos_dir == "short" and current_spread >= 0.0 and bars_held >= self.min_hold_bars:
                self.prev_spread = current_spread
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": current_spread, "bars_held": bars_held}
                )

            self.prev_spread = current_spread
            return None

        # Entry gating: Cooldown enforced to prevent rapid overtrading and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            self.prev_spread = current_spread
            return None

        # Discrete event-study trigger: upward threshold crossing of spread
        # ETH outperforming BTC by > 2.2% with fresh crossing
        long_cross = (self.prev_spread <= self.spread_threshold) and (current_spread > self.spread_threshold)
        short_cross = (self.prev_spread >= -self.spread_threshold) and (current_spread < -self.spread_threshold)

        sig = None
        if long_cross and close > ema20:
            confidence = min(0.9, 0.5 + (current_spread - self.spread_threshold) * 0.1)
            self.entry_bar = ctx.bar_index
            sig = ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outperformance_event_cross",
                    "spread": current_spread,
                    "prev_spread": self.prev_spread,
                    "close": close,
                    "ema20": ema20,
                    "atr": atr,
                }
            )
        elif short_cross and close < ema20:
            confidence = min(0.9, 0.5 + abs(current_spread + self.spread_threshold) * 0.1)
            self.entry_bar = ctx.bar_index
            sig = ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_underperformance_event_cross",
                    "spread": current_spread,
                    "prev_spread": self.prev_spread,
                    "close": close,
                    "ema20": ema20,
                    "atr": atr,
                }
            )

        self.prev_spread = current_spread
        return sig