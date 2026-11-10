from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours target horizon
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0      # 2.0% 24h return out/under-performance
        self.reversion_threshold = 0.4   # mean reversion target near zero
        self.cooldown_bars = 6          # mandatory cooldown between trades
        self.last_exit_bar = -100
        self.max_bars_in_trade = 14     # structural timed exit to avoid holdout decay
        self.entry_bar_index = 0

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
        if len(closes) < 30:
            return None

        # Cross-asset relative performance spread
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Volatility assessment for dynamic risk buffer
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        atr_pct = (atr / closes[-1]) if atr and closes[-1] > 0 else 0.015
        
        # Volatility-scaled dynamic stops (bounded to avoid allocation-level trailing stop traps)
        dynamic_sl_bps = max(180.0, min(360.0, atr_pct * 15000.0))
        dynamic_tp_bps = dynamic_sl_bps * 1.5

        # Check existing positions for mean reversion or timed exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_index
            pos_dir = ctx.position_direction()

            # Structural time-based risk cut
            if bars_held >= self.max_bars_in_trade:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "timed_exit_max_bars", "bars_held": bars_held, "spread": round(spread, 3)}
                )

            # Economic thesis exit: spread reverted to neutral range
            if pos_dir == "long" and spread <= self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": round(spread, 3), "bars_held": bars_held}
                )
            elif pos_dir == "short" and spread >= -self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": round(spread, 3), "bars_held": bars_held}
                )
            return None

        # Cooldown guard after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering trend momentum against crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price confirmation: ensure local momentum aligns with relative strength
        short_ma = sum(closes[-6:]) / 6.0
        med_ma = sum(closes[-18:]) / 18.0
        price = closes[-1]

        # Long entry: ETH significantly leading BTC, confirmed by local price structure
        if spread >= self.spread_threshold and price > short_ma >= med_ma:
            self.entry_bar_index = ctx.bar_index
            conf = min(0.9, 0.55 + min(abs(spread) / 10.0, 0.35))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "eth_relative_strength_lead",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "price": round(price, 2),
                    "dynamic_sl_bps": round(dynamic_sl_bps, 1),
                }
            )

        # Short entry: ETH heavily lagging BTC, confirmed by local price breakdown
        if spread <= -self.spread_threshold and price < short_ma <= med_ma:
            self.entry_bar_index = ctx.bar_index
            conf = min(0.9, 0.55 + min(abs(spread) / 10.0, 0.35))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "eth_relative_weakness_lag",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "price": round(price, 2),
                    "dynamic_sl_bps": round(dynamic_sl_bps, 1),
                }
            )

        return None