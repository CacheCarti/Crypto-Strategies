from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthDonchianFadeRegime(Strategy):
    METADATA = {
        "name": "EthDonchianFadeRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        total_needed = max(self.channel_period + 2, self.atr_period + 2)
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Time-based exit guard
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate based on prior evidence: avoid Meltdown/Crisis
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        # Donchian channel formed by the previous 20 completed bars (excluding current bar)
        prev_highs = highs[-self.channel_period - 1:-1]
        prev_lows = lows[-self.channel_period - 1:-1]
        donchian_high = max(prev_highs)
        donchian_low = min(prev_lows)

        # Require a meaningful intrusion beyond the level to signify a real liquidity sweep (>= 0.25 ATR)
        min_penetration = 0.25 * atr
        price_eps = 1e-6

        # Bear Trap -> Long setup (poke below 20-bar low, close back above it)
        if current_low < (donchian_low - min_penetration) and current_close > (donchian_low + price_eps):
            wick_size = donchian_low - current_low
            stop_dist = max(wick_size, atr * 0.5)
            sl_bps = min(max((stop_dist / current_close) * 10000.0, 120.0), 300.0)
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "bear_trap_donchian_fade",
                    "donchian_low": round(donchian_low, 2),
                    "penetration": round(wick_size, 2),
                    "atr": round(atr, 2),
                    "regime": market_regime
                }
            )

        # Bull Trap -> Short setup (poke above 20-bar high, close back below it)
        if current_high > (donchian_high + min_penetration) and current_close < (donchian_high - price_eps):
            wick_size = current_high - donchian_high
            stop_dist = max(wick_size, atr * 0.5)
            sl_bps = min(max((stop_dist / current_close) * 10000.0, 120.0), 300.0)
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "bull_trap_donchian_fade",
                    "donchian_high": round(donchian_high, 2),
                    "penetration": round(wick_size, 2),
                    "atr": round(atr, 2),
                    "regime": market_regime
                }
            )

        return None