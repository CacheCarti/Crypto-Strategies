from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryBTC(Strategy):
    METADATA = {
        "name": "CrashRecoveryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 14

    def _stddev(self, values: list) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        prev_high = highs[-2]
        
        # Calculate recent return profile over the lookback window
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        drawdown_pct = (current_close - window_high) / window_high

        # Relative volatility measure: rolling standard deviation of 1h returns
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        vol = self._stddev(returns[-self.lookback:])
        vol_threshold = max(0.045, 2.0 * vol * math.sqrt(self.lookback))

        # Check existing position for take profit at midpoint or timeout
        if ctx.has_position():
            midpoint = (window_high + window_low) / 2.0
            bars_in_trade = ctx.bar_index - self.entry_bar
            
            if current_close >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "exit_reached_midpoint",
                        "midpoint": midpoint,
                        "current_close": current_close,
                        "bars_held": bars_in_trade,
                    }
                )
            
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "exit_max_hold_timeout",
                        "bars_held": bars_in_trade,
                        "current_close": current_close,
                    }
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Condition 1: Significant flush (adaptive: min 4.5% or 2x historical rolling vol)
        is_flushed = drawdown_pct <= -vol_threshold

        # Condition 2: Capitulation reversal candle (current bar takes out previous bar's high)
        reversal_trigger = (current_close > prev_high) and (closes[-2] < closes[-3])

        # Condition 3: Market/sentiment context filter (not in extreme mania)
        fg_index = ctx.features.get("fear_greed_index", 50)
        sentiment_ok = fg_index < 70

        if is_flushed and reversal_trigger and sentiment_ok:
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, max(0.5, abs(drawdown_pct) * 8.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "flush_capitulation_reversal",
                    "drawdown_pct": round(drawdown_pct, 4),
                    "vol_threshold": round(vol_threshold, 4),
                    "prev_high": prev_high,
                    "close": current_close,
                    "fear_greed": fg_index,
                }
            )

        return None