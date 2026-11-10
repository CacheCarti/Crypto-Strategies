from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryBTC(Strategy):
    METADATA = {
        "name": "CrashRecoveryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 40,
        "required_features": ["fear_greed_index", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.05
        self.cooldown_bars = 16
        self.max_hold_bars = 14
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_reference_high = 0.0
        self.entry_reference_low = 0.0

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
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Manage existing position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Target 1: Reaching midpoint of the crash window
            midpoint = (self.entry_reference_high + self.entry_reference_low) / 2.0
            if current_close >= midpoint and midpoint > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_reached",
                        "midpoint": midpoint,
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )

            # Target 2: Max time horizon reached
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_stop_reversion_expired",
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Lookback flush calculation
        window_high = max(highs[-self.lookback - 1 : -1])
        window_low = min(lows[-self.lookback - 1 : -1])
        if window_high <= 0:
            return None

        flush_drop = (window_high - window_low) / window_high
        if flush_drop < self.drop_threshold:
            return None

        # Reversal trigger: current close breaks above previous bar's high
        prev_high = highs[-2]
        curr_close = closes[-1]
        if curr_close <= prev_high:
            return None

        # Volatility state confirmation: ATR expansion indicates capitulation volume/spread
        atr_curr = self._atr(highs, lows, closes, 14)
        atr_base = self._atr(highs[:-6], lows[:-6], closes[:-6], 14)
        if atr_curr is None or atr_base is None or atr_base == 0:
            return None
        if atr_curr < atr_base * 1.05:
            return None

        # Cross-asset confirmation: ETH 24h return is stabilizing / not in free-fall divergence
        eth_return = ctx.features.get("eth_return_pct", 0.0)
        if eth_return < -18.0:
            return None

        # Macro fear check: avoid standard shallow pullbacks, demand real stress
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed > 45.0:
            return None

        # Record entry reference points
        self.entry_bar = ctx.bar_index
        self.entry_reference_high = window_high
        self.entry_reference_low = window_low

        confidence = 0.70
        if fear_greed < 25.0:
            confidence = 0.85

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            metadata={
                "reason": "flush_capitulation_breakout_confirmed",
                "flush_drop_pct": round(flush_drop * 100, 2),
                "fear_greed": fear_greed,
                "atr_ratio": round(atr_curr / atr_base, 2),
                "eth_24h_ret": eth_return,
                "prev_high": prev_high,
                "close": curr_close
            }
        )