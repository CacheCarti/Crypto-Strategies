from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 8
        self.persist_bars = 3
        self.funding_long_threshold = -0.00015
        self.funding_short_threshold = 0.00025
        self.funding_exit_neutral = 0.00005

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Regimes from market context
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Avoid fading during crisis / meltdown states
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        # Manage existing open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_funding >= -self.funding_exit_neutral:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_long", "funding": current_funding})
            elif pos_dir == "short" and current_funding <= self.funding_exit_neutral:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_short", "funding": current_funding})
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Ensure funding persistence
        if len(self.funding_history) < self.persist_bars:
            return None

        recent_funding = self.funding_history[-self.persist_bars:]
        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)

        if ema20 is None or rsi is None:
            return None

        price = ctx.bar.close

        # Long entry: persistent negative funding (crowd short) + not in freefall
        if all(f <= self.funding_long_threshold for f in recent_funding):
            if price > ema20 * 0.965 and rsi > 30.0:
                conf = min(0.9, 0.6 + abs(current_funding) * 1000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "funding_squeeze_long",
                        "funding": current_funding,
                        "rsi": rsi,
                        "ema20": ema20,
                        "price": price,
                    }
                )

        # Short entry: persistent positive funding (crowd over-leveraged long) + not in vertical breakout
        if all(f >= self.funding_short_threshold for f in recent_funding):
            if price < ema20 * 1.035 and rsi < 70.0:
                conf = min(0.9, 0.6 + abs(current_funding) * 1000.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "funding_overbought_short",
                        "funding": current_funding,
                        "rsi": rsi,
                        "ema20": ema20,
                        "price": price,
                    }
                )

        return None