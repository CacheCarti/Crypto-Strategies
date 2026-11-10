from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 168
        self.channel_period = 12
        self.cooldown_period = 8
        self.spike_threshold = 1.75
        
        self.had_spike = False
        self.coiled = False
        self.last_exit_bar = -100

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
        self.had_spike = False
        self.coiled = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback + 15
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Compute rolling 14-bar true ranges to estimate volatility over lookback
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)

        # Build rolling 14-period ATR array
        rolling_atrs = []
        for i in range(14, len(trs) + 1):
            rolling_atrs.append(sum(trs[i - 14:i]) / 14.0)

        if len(rolling_atrs) < self.lookback:
            return None

        hist_atrs = rolling_atrs[-self.lookback:]
        sorted_atrs = sorted(hist_atrs)
        median_atr = sorted_atrs[len(sorted_atrs) // 2]
        current_atr = rolling_atrs[-1]

        if median_atr <= 0:
            return None

        # Check for spike in recent 36 bars
        recent_window = hist_atrs[-36:]
        recent_max_atr = max(recent_window)

        if recent_max_atr >= self.spike_threshold * median_atr:
            self.had_spike = True

        # Coil state: after spike, volatility collapses back at or below median
        if self.had_spike and current_atr <= median_atr:
            self.coiled = True

        # If already in a position, manage exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_p = ctx.position.get("entry_price", ctx.bar.close) if ctx.position else ctx.bar.close
            
            # Trailing/time-based sanity exit if volatility completely dies or flips
            if pos_dir == "long" and ctx.bar.close < min(lows[-4:]):
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "trail_exit_long", "close": ctx.bar.close})
            elif pos_dir == "short" and ctx.bar.close > max(highs[-4:]):
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "trail_exit_short", "close": ctx.bar.close})
            return None

        # Only evaluate breakout entry if compression coil is active
        if self.coiled:
            # 12-bar Donchian breakout (excluding current bar)
            prior_highs = highs[-self.channel_period - 1:-1]
            prior_lows = lows[-self.channel_period - 1:-1]
            channel_high = max(prior_highs)
            channel_low = min(prior_lows)

            # Cross-asset trend check via market context to avoid false breakouts
            trend_regime = ctx.market.get("trend_regime", "neutral")
            btc_ret = ctx.market.get("btc_return_pct", 0.0)

            # Long breakout
            if ctx.bar.close > channel_high and ctx.bar.close > ctx.bar.open:
                if trend_regime != "bear" and btc_ret > -2.5:
                    self.coiled = False
                    self.had_spike = False
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "coil_breakout_long",
                            "close": ctx.bar.close,
                            "channel_high": channel_high,
                            "current_atr": current_atr,
                            "median_atr": median_atr,
                            "btc_ret": btc_ret,
                        }
                    )

            # Short breakout
            elif ctx.bar.close < channel_low and ctx.bar.close < ctx.bar.open:
                if trend_regime != "bull" and btc_ret < 2.5:
                    self.coiled = False
                    self.had_spike = False
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "coil_breakout_short",
                            "close": ctx.bar.close,
                            "channel_low": channel_low,
                            "current_atr": current_atr,
                            "median_atr": median_atr,
                            "btc_ret": btc_ret,
                        }
                    )

        return None