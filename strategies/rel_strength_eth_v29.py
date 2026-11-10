from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_history = []
        self.history_len = 72
        self.last_trade_bar = -999
        self.cooldown_bars = 6
        self.bars_in_pos = 0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track spread between ETH and BTC 24h returns
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret
        
        self.spread_history.append(spread)
        if len(self.spread_history) > self.history_len:
            self.spread_history.pop(0)

        # Rolling z-score of spread to avoid brittle fixed thresholds
        if len(self.spread_history) < 24:
            return None

        mean_spread = sum(self.spread_history) / len(self.spread_history)
        variance = sum((s - mean_spread) ** 2 for s in self.spread_history) / len(self.spread_history)
        std_spread = math.sqrt(variance) if variance > 1e-6 else 0.001
        z_spread = (spread - mean_spread) / std_spread

        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        current_close = closes[-1]

        # Position management
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()

            # Mean-reversion exit on spread normalizing or trend breaking
            if direction == "long" and (z_spread < 0.2 or (ema20 is not None and current_close < ema20)):
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_reverted_or_ema_break_long",
                    "z_spread": round(z_spread, 3),
                    "spread": round(spread, 3),
                    "bars_in_pos": self.bars_in_pos,
                })
            elif direction == "short" and (z_spread > -0.2 or (ema20 is not None and current_close > ema20)):
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_reverted_or_ema_break_short",
                    "z_spread": round(z_spread, 3),
                    "spread": round(spread, 3),
                    "bars_in_pos": self.bars_in_pos,
                })
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Avoid entries during acute market meltdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or current_close == 0:
            return None

        vol_bps = (atr / current_close) * 10000.0
        sl_bps = max(180.0, min(380.0, vol_bps * 1.5))
        tp_bps = sl_bps * 1.6

        # Entry logic: Robust percentile/z-score rotation + trend confirmation
        if ema20 is not None and ema50 is not None:
            # Long: ETH leading significantly, confirmed by price above EMA20 and upward slope
            if z_spread > 1.6 and spread > 1.0 and current_close > ema20 and ema20 > ema50:
                self.last_trade_bar = ctx.bar_index
                self.bars_in_pos = 0
                conf = min(0.9, 0.55 + (z_spread - 1.6) * 0.15)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_relative_strength_breakout",
                        "z_spread": round(z_spread, 3),
                        "spread": round(spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                        "ema20": round(ema20, 2),
                    }
                )

            # Short: ETH lagging significantly, confirmed by price below EMA20 and downward slope
            elif z_spread < -1.6 and spread < -1.0 and current_close < ema20 and ema20 < ema50:
                self.last_trade_bar = ctx.bar_index
                self.bars_in_pos = 0
                conf = min(0.9, 0.55 + (abs(z_spread) - 1.6) * 0.15)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "eth_relative_weakness_breakdown",
                        "z_spread": round(z_spread, 3),
                        "spread": round(spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                        "ema20": round(ema20, 2),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.bars_in_pos = 0