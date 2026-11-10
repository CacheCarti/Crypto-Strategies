from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 6
        self.max_hold_bars = 5
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        closes = ctx.closes(self.lookback + 15)
        highs = ctx.highs(self.lookback + 15)
        lows = ctx.lows(self.lookback + 15)

        if len(closes) < self.lookback + 15:
            return None

        # Manage open position duration exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_reversion_window_expired",
                        "bars_held": bars_held,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Mandatory cooldown filter
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety: avoid entering against outright crisis / meltdown spikes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar channel (excluding current bar)
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        prior_max = max(prior_highs)
        prior_min = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        # Minimum wick threshold to qualify as a distinct liquidity run/stop-hunt
        min_penetration = 0.35 * atr

        # Bull Trap / High Liquidity Sweep:
        # Poked meaningfully above prior 20-bar resistance, but closed back firmly inside
        if curr_high >= prior_max + min_penetration and curr_close < prior_max:
            wick_size = curr_high - max(curr_close, ctx.bar.open)
            body_size = abs(curr_close - ctx.bar.open)
            if wick_size >= body_size:  # Rejection candle structure
                self.entry_bar = ctx.bar_index
                sl_bps = min(300.0, max(140.0, ((curr_high - curr_close) / curr_close) * 10000.0 + 30.0))
                tp_bps = sl_bps * 1.55
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "failed_breakout_high_wick_rejection",
                        "prior_max": prior_max,
                        "curr_high": curr_high,
                        "penetration_atr": (curr_high - prior_max) / atr,
                        "close": curr_close
                    }
                )

        # Bear Trap / Low Liquidity Sweep:
        # Poked meaningfully below prior 20-bar support, but closed back firmly inside
        if curr_low <= prior_min - min_penetration and curr_close > prior_min:
            wick_size = min(curr_close, ctx.bar.open) - curr_low
            body_size = abs(curr_close - ctx.bar.open)
            if wick_size >= body_size:  # Rejection candle structure
                self.entry_bar = ctx.bar_index
                sl_bps = min(300.0, max(140.0, ((curr_close - curr_low) / curr_close) * 10000.0 + 30.0))
                tp_bps = sl_bps * 1.55
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "failed_breakout_low_wick_rejection",
                        "prior_min": prior_min,
                        "curr_low": curr_low,
                        "penetration_atr": (prior_min - curr_low) / atr,
                        "close": curr_close
                    }
                )

        return None