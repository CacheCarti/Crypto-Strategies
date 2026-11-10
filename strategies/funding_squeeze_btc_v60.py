from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeTrap(Strategy):
    METADATA = {
        "name": "Funding Squeeze Trap",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.last_trade_bar = -100
        self.funding_threshold = 0.00003  # 0.003% bias threshold
        self.momentum_threshold_bps = 70.0  # 0.70% price displacement over 24h

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
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Cooldown guard: enforce post-exit pause and inter-trade spacing
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Avoid entering during crisis or extreme volatility
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        curr_price = closes[-1]
        past_price = closes[-self.lookback]
        prev_price = closes[-2]
        prev_past_price = closes[-self.lookback - 1]

        # 24-hour price displacement in bps
        price_change_bps = ((curr_price - past_price) / past_price) * 10000.0
        prev_change_bps = ((prev_price - prev_past_price) / prev_past_price) * 10000.0

        # Event boundary: Trigger exactly when price crosses displacement threshold while funding disagrees
        bull_trap_trigger = (
            price_change_bps >= self.momentum_threshold_bps
            and prev_change_bps < self.momentum_threshold_bps
            and funding <= -self.funding_threshold
        )
        bear_trap_trigger = (
            price_change_bps <= -self.momentum_threshold_bps
            and prev_change_bps > -self.momentum_threshold_bps
            and funding >= self.funding_threshold
        )

        # Volatility check: require minimum active volatility to avoid drift
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes[-20:], period=14)
        if atr is None or (atr / curr_price) < 0.002:
            return None

        # Position handling
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Dynamic divergence resolution exit
            if pos_dir == "long" and funding > 0.0001:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_long_exit", "funding": funding})
            elif pos_dir == "short" and funding < -0.0001:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_short_exit", "funding": funding})
            return None

        # Entry logic
        if bull_trap_trigger:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(funding) * 2000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "short_squeeze_divergence_entry",
                    "funding_rate": funding,
                    "displacement_bps": price_change_bps,
                    "price": curr_price,
                    "atr": atr,
                },
            )

        if bear_trap_trigger:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(funding) * 2000.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "long_trap_divergence_entry",
                    "funding_rate": funding,
                    "displacement_bps": price_change_bps,
                    "price": curr_price,
                    "atr": atr,
                },
            )

        return None