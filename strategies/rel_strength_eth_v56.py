from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.cooldown_period = 6
        self.last_trade_bar = -999
        self.bars_in_pos = 0

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
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret
        fg_index = ctx.features.get("fear_greed_index", 50)

        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or rsi14 is None:
            return None

        current_close = ctx.bar.close

        # Position management
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()

            # Mean-reversion exit condition of the spread or RSI exhaustion
            if direction == "long":
                if spread < 0.25 or rsi14 > 72.0 or self.bars_in_pos >= 16:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted_or_exhausted",
                            "spread": round(spread, 3),
                            "rsi": round(rsi14, 2),
                            "bars_held": self.bars_in_pos,
                        }
                    )
            elif direction == "short":
                if spread > -0.25 or rsi14 < 28.0 or self.bars_in_pos >= 16:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted_or_exhausted",
                            "spread": round(spread, 3),
                            "rsi": round(rsi14, 2),
                            "bars_held": self.bars_in_pos,
                        }
                    )
            return None

        # Mandatory cooldown guard between exits and entries
        if ctx.bar_index - self.last_trade_bar < self.cooldown_period:
            return None

        # Filter crisis or meltdown regimes to avoid gap-risk
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Relative strength long setup
        if spread >= self.spread_threshold and current_close > ema20 and 45.0 <= rsi14 <= 66.0:
            self.last_trade_bar = ctx.bar_index
            self.bars_in_pos = 0
            conf = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_btc_rel_strength_breakout_long",
                    "spread": round(spread, 3),
                    "rsi": round(rsi14, 2),
                    "ema20": round(ema20, 2),
                    "close": round(current_close, 2),
                    "fg_index": fg_index,
                }
            )

        # Relative weakness short setup
        if spread <= -self.spread_threshold and current_close < ema20 and 34.0 <= rsi14 <= 55.0:
            self.last_trade_bar = ctx.bar_index
            self.bars_in_pos = 0
            conf = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_btc_rel_weakness_breakdown_short",
                    "spread": round(spread, 3),
                    "rsi": round(rsi14, 2),
                    "ema20": round(ema20, 2),
                    "close": round(current_close, 2),
                    "fg_index": fg_index,
                }
            )

        return None