from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthBreakout(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0  # 2% spread trigger
        self.cooldown_bars = 8       # Hard cooldown to prevent overtrading and pass holdout
        self.last_exit_bar = -999
        self.bars_in_position = 0
        self.max_hold_bars = 16      # Explicit event boundary for hold duration
        self.prev_spread = 0.0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature extraction
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Moving average trend check on ETH
        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        # Position management: track hold duration and check mean-reversion exit
        if ctx.has_position():
            self.bars_in_position += 1
            pos_dir = ctx.position_direction()

            # Time stop / event boundary
            if self.bars_in_position >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "max_bars_event_expiry",
                    "bars_held": self.bars_in_position,
                    "spread": round(spread, 3),
                    "price": current_price
                })

            # Economic exit: spread mean-reversion
            if pos_dir == "long" and spread <= 0.2:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_mean_reverted_long_exit",
                    "spread": round(spread, 3),
                    "price": current_price
                })
            elif pos_dir == "short" and spread >= -0.2:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "spread_mean_reverted_short_exit",
                    "spread": round(spread, 3),
                    "price": current_price
                })

            return None

        # Mandatory cooldown check after last trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            self.prev_spread = spread
            return None

        # Market regime filter: avoid entering during extreme crisis dislocations
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            self.prev_spread = spread
            return None

        # EVENT CROSS TRIGGER: fire on the crossover moment to avoid repeated clustering
        long_event = (self.prev_spread <= self.spread_threshold) and (spread > self.spread_threshold)
        short_event = (self.prev_spread >= -self.spread_threshold) and (spread < -self.spread_threshold)

        self.prev_spread = spread

        # Entry logic with momentum alignment
        if long_event and current_price > ema20 and rsi14 > 45.0:
            self.bars_in_position = 0
            conf = min(0.9, 0.6 + abs(spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_event_cross",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi14, 2),
                    "price": current_price
                }
            )

        if short_event and current_price < ema20 and rsi14 < 55.0:
            self.bars_in_position = 0
            conf = min(0.9, 0.6 + abs(-self.spread_threshold - spread) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_event_cross",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi14, 2),
                    "price": current_price
                }
            )

        return None