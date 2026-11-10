from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentumBreakout(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentumBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.prev_spread = 0.0

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
        closes = ctx.closes(40)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < 40 or len(highs) < 20 or len(lows) < 20:
            return None

        current_price = ctx.bar.close
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Track spread state crossing to avoid repeated signals
        crossed_up = (self.prev_spread <= self.spread_threshold) and (spread > self.spread_threshold)
        crossed_down = (self.prev_spread >= -self.spread_threshold) and (spread < -self.spread_threshold)
        self.prev_spread = spread

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_in_trade = ctx.bar_index - self.entry_bar

            # Mean-reversion exit or max hold time
            if pos_dir == "long" and (spread <= 0.0 or bars_in_trade >= self.max_hold_bars):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_reverted_or_max_hold",
                    "spread": spread,
                    "bars_in_trade": bars_in_trade,
                    "price": current_price
                })
            elif pos_dir == "short" and (spread >= 0.0 or bars_in_trade >= self.max_hold_bars):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_reverted_or_max_hold",
                    "spread": spread,
                    "bars_in_trade": bars_in_trade,
                    "price": current_price
                })
            return None

        # Enforce cooldown after prior trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Price confirmation via EMA trend & volatility
        ema_fast = self._ema(closes, 12)
        ema_slow = self._ema(closes, 26)
        atr = self._atr(highs, lows, closes, 14)

        if ema_fast is None or ema_slow is None or atr is None:
            return None

        atr_pct = (atr / current_price) * 10000.0
        if atr_pct < 25.0:  # Skip dead ranges
            return None

        # Entry logic: strict threshold crossing event + EMA trend alignment
        if crossed_up and ema_fast > ema_slow and current_price > ema_fast:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + min(abs(spread) / 10.0, 0.25))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_rel_strength_breakout_cross",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price
                }
            )

        if crossed_down and ema_fast < ema_slow and current_price < ema_fast:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + min(abs(spread) / 10.0, 0.25))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_rel_weakness_breakout_cross",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price
                }
            )

        return None