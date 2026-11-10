from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 5
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period + 2:
            return None

        current_idx = ctx.bar_index

        # Time-based exit
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Exclude extreme regime meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate Donchian bounds excluding current bar
        prior_highs = highs[-self.channel_period - 1:-1]
        prior_lows = lows[-self.channel_period - 1:-1]
        dh = max(prior_highs)
        dl = min(prior_lows)

        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None or atr_val <= 0:
            return None

        cur_high = ctx.bar.high
        cur_low = ctx.bar.low
        cur_close = ctx.bar.close
        min_penetration = 0.20 * atr_val

        # Cross-asset momentum confirmation from BTC
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Bull Trap (Failed Upside Breakout) -> Go Short
        # Intrabar poke clearly pierced the 20-bar high, but bar settled back below it
        if cur_high > dh + min_penetration and cur_close < dh:
            # Confirm market weakness / lack of broad bull breakout
            if btc_ret < 1.5:
                wick_size = cur_high - max(ctx.bar.open, cur_close)
                if wick_size >= min_penetration:
                    self.entry_bar = current_idx
                    sl_dist = (cur_high - cur_close) / cur_close * 10000.0
                    sl_bps = max(100.0, min(self.METADATA["declared_sl_bps"], sl_dist + 20.0))
                    tp_bps = self.METADATA["declared_tp_bps"]
                    
                    return ctx.signal(
                        "short",
                        confidence=0.72,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        metadata={
                            "reason": "failed_breakout_wick_reversal_short",
                            "donchian_high": dh,
                            "high": cur_high,
                            "close": cur_close,
                            "penetration": cur_high - dh,
                            "btc_ret": btc_ret,
                            "atr": atr_val
                        }
                    )

        # Bear Trap (Failed Downside Breakout) -> Go Long
        # Intrabar poke clearly pierced the 20-bar low, but bar settled back above it
        if cur_low < dl - min_penetration and cur_close > dl:
            # Confirm market stability / lack of broad dump
            if btc_ret > -1.5:
                wick_size = min(ctx.bar.open, cur_close) - cur_low
                if wick_size >= min_penetration:
                    self.entry_bar = current_idx
                    sl_dist = (cur_close - cur_low) / cur_close * 10000.0
                    sl_bps = max(100.0, min(self.METADATA["declared_sl_bps"], sl_dist + 20.0))
                    tp_bps = self.METADATA["declared_tp_bps"]

                    return ctx.signal(
                        "long",
                        confidence=0.72,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        metadata={
                            "reason": "failed_breakout_wick_reversal_long",
                            "donchian_low": dl,
                            "low": cur_low,
                            "close": cur_close,
                            "penetration": dl - cur_low,
                            "btc_ret": btc_ret,
                            "atr": atr_val
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index