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
        self.wick_atr_mult = 0.25
        self.cooldown_bars = 4
        self.max_hold_bars = 8
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
        closes = ctx.closes(self.channel_period + 5)
        highs = ctx.highs(self.channel_period + 5)
        lows = ctx.lows(self.channel_period + 5)

        if len(closes) < self.channel_period + 2:
            return None

        current_idx = ctx.bar_index
        has_pos = ctx.has_position()

        # Manage existing position with time-based exit
        if has_pos:
            bars_held = current_idx - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_exit_reached", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown guard
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Volatility regime: avoid trading in extreme crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar extremes (excluding current forming/closed bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        curr_high = highs[-1]
        curr_low = lows[-1]
        curr_close = closes[-1]
        curr_open = ctx.bar.open

        min_wick = atr * self.wick_atr_mult
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Bull Trap / Upward Failed Breakout:
        # Intrabar poke cleanly above prior 20-bar high by at least min_wick, but close falls back below prior high.
        # Cross-asset confirmation: BTC is not in a runaway melt-up (btc_return_pct < 2.5) to avoid steamroller trends.
        if curr_high >= channel_high + min_wick and curr_close < channel_high and curr_close <= curr_open:
            if btc_ret < 2.5:
                # Dynamic stop-loss anchored above the rejection wick
                wick_excess_bps = ((curr_high - curr_close) / curr_close) * 10000.0
                dynamic_sl = max(140.0, min(wick_excess_bps + 30.0, 260.0))
                dynamic_tp = dynamic_sl * 1.5

                self.entry_bar = current_idx
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=dynamic_sl,
                    take_profit_bps=dynamic_tp,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "failed_breakout_short",
                        "channel_high": channel_high,
                        "poke_high": curr_high,
                        "close": curr_close,
                        "btc_return_pct": btc_ret,
                        "atr": atr
                    }
                )

        # Bear Trap / Downward Failed Breakout:
        # Intrabar flush below prior 20-bar low by at least min_wick, but close recovers back inside the channel.
        # Cross-asset confirmation: BTC is not in severe freefall (btc_return_pct > -2.5).
        if curr_low <= channel_low - min_wick and curr_close > channel_low and curr_close >= curr_open:
            if btc_ret > -2.5:
                wick_excess_bps = ((curr_close - curr_low) / curr_close) * 10000.0
                dynamic_sl = max(140.0, min(wick_excess_bps + 30.0, 260.0))
                dynamic_tp = dynamic_sl * 1.5

                self.entry_bar = current_idx
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=dynamic_sl,
                    take_profit_bps=dynamic_tp,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "failed_breakout_long",
                        "channel_low": channel_low,
                        "flush_low": curr_low,
                        "close": curr_close,
                        "btc_return_pct": btc_ret,
                        "atr": atr
                    }
                )

        return None