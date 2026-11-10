from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeTrap(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.max_hold_bars = 6
        self.cooldown_period = 6
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
        closes = ctx.closes(self.channel_period + 10)
        highs = ctx.highs(self.channel_period + 10)
        lows = ctx.lows(self.channel_period + 10)

        if len(closes) < self.channel_period + 5:
            return None

        # Check open positions for timed structural exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_fade_exit", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Mandatory cooldown to prevent overtrading and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # 20-bar Donchian channel evaluated strictly prior to the current bar
        prior_highs = highs[-self.channel_period - 1:-1]
        prior_lows = lows[-self.channel_period - 1:-1]
        donchian_high = max(prior_highs)
        donchian_low = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close
        min_penetration = atr * 0.25

        # Avoid fading traps during severe systemic market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Bull Trap / Bearish Fade: High swept above channel by meaningful wick, closed back inside
        if curr_high > (donchian_high + min_penetration) and curr_close < donchian_high:
            # Wick must represent significant rejection
            upper_wick = curr_high - max(curr_close, ctx.bar.open)
            body = abs(curr_close - ctx.bar.open)
            if upper_wick >= body * 0.8:
                risk_dist = curr_high - curr_close
                sl_bps = max(120.0, min(350.0, (risk_dist / curr_close) * 10000.0 * 1.1))
                tp_bps = sl_bps * 1.6
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bull_trap_wick_rejection",
                        "donchian_high": donchian_high,
                        "wick_high": curr_high,
                        "atr": atr,
                        "sl_bps": sl_bps
                    }
                )

        # Bear Trap / Bullish Fade: Low swept below channel by meaningful wick, closed back inside
        if curr_low < (donchian_low - min_penetration) and curr_close > donchian_low:
            # Lower wick must represent significant buying absorption
            lower_wick = min(curr_close, ctx.bar.open) - curr_low
            body = abs(curr_close - ctx.bar.open)
            if lower_wick >= body * 0.8:
                risk_dist = curr_close - curr_low
                sl_bps = max(120.0, min(350.0, (risk_dist / curr_close) * 10000.0 * 1.1))
                tp_bps = sl_bps * 1.6
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bear_trap_wick_rejection",
                        "donchian_low": donchian_low,
                        "wick_low": curr_low,
                        "atr": atr,
                        "sl_bps": sl_bps
                    }
                )

        return None