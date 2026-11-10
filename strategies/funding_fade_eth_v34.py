from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.entry_bar = -999

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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Track persistent funding rate
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Skip during crisis meltdown to prevent fading catastrophic drops
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        # Technical context
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or ema50 is None or rsi14 is None:
            return None

        price = ctx.bar.close

        # Position management: exit on funding normalization or trend breakdown
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Funding has normalized
            if pos_dir == "long" and current_funding >= 0.00003:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_long", "funding": current_funding, "rsi": rsi14})
            if pos_dir == "short" and current_funding <= -0.00003:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_short", "funding": current_funding, "rsi": rsi14})

            # Reversal protection
            if pos_dir == "long" and rsi14 > 72.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "overbought_take_profit", "rsi": rsi14})
            if pos_dir == "short" and rsi14 < 28.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "oversold_take_profit", "rsi": rsi14})

            return None

        # Entry logic with cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if len(self.funding_history) < 3:
            return None

        avg_recent_funding = sum(self.funding_history[-3:]) / 3.0

        # Long Setup: Persistent negative funding (crowd short) + not in severe crash
        # Require price above recent low support or turning up, avoiding runaway breakdown
        if (avg_recent_funding <= -0.00015 and current_funding <= -0.00010):
            # Avoid fading if price is crashing exponentially (dist from EMA50 > 6%)
            dist_ema50 = (price - ema50) / ema50
            if dist_ema50 > -0.05 and rsi14 >= 30.0 and rsi14 <= 55.0:
                conf = min(0.9, 0.5 + abs(avg_recent_funding) * 1000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=280.0,
                    take_profit_bps=420.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "negative_funding_squeeze",
                        "avg_funding": avg_recent_funding,
                        "current_funding": current_funding,
                        "rsi": rsi14,
                        "ema50_dist": dist_ema50,
                    }
                )

        # Short Setup: Persistent positive funding (crowd excessively leveraged long)
        if (avg_recent_funding >= 0.00020 and current_funding >= 0.00015):
            dist_ema50 = (price - ema50) / ema50
            if dist_ema50 < 0.05 and rsi14 <= 70.0 and rsi14 >= 45.0:
                conf = min(0.9, 0.5 + avg_recent_funding * 1000.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=280.0,
                    take_profit_bps=420.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "positive_funding_exhaustion",
                        "avg_funding": avg_recent_funding,
                        "current_funding": current_funding,
                        "rsi": rsi14,
                        "ema50_dist": dist_ema50,
                    }
                )

        return None