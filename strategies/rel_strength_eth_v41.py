from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RelStrengthEthMomentum(Strategy):
    METADATA = {
        "name": "RelStrengthEthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.0
        self.reversion_threshold = 0.5
        self.max_hold_bars = 16

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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Feature readings
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Indicators
        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema20 is None or rsi is None:
            return None

        curr_close = ctx.bar.close

        # Position Management
        if ctx.has_position():
            pos = ctx.position
            direction = pos.get("direction") if pos else ctx.position_direction()
            
            # Mean-reversion exit or RSI extreme exhaustion
            if direction == "long":
                if spread <= self.reversion_threshold or rsi > 74.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted_or_rsi_overbought",
                            "spread": spread,
                            "rsi": rsi,
                            "price": curr_close,
                        }
                    )
            elif direction == "short":
                if spread >= -self.reversion_threshold or rsi < 26.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted_or_rsi_oversold",
                            "spread": spread,
                            "rsi": rsi,
                            "price": curr_close,
                        }
                    )
            return None

        # Enforce post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Entry logic: Strong relative momentum aligned with price trend & non-exhausted RSI
        # Long Setup: ETH outperforming BTC by > 2.0%, price above EMA20, RSI healthy (44-68)
        if spread >= self.spread_threshold and curr_close > ema20 and (44.0 <= rsi <= 68.0):
            conf = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=520.0,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rel_strength_eth_momentum_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema20": ema20,
                    "price": curr_close,
                }
            )

        # Short Setup: ETH lagging BTC by > 2.0%, price below EMA20, RSI healthy (32-56)
        if spread <= -self.spread_threshold and curr_close < ema20 and (32.0 <= rsi <= 56.0):
            conf = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=520.0,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rel_weakness_eth_momentum_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema20": ema20,
                    "price": curr_close,
                }
            )

        return None