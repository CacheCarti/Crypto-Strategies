from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class PanicRecoveryReversal(Strategy):
    METADATA = {
        "name": "PanicRecoveryReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 43200,  # 12 hours nominal hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.055  # 5.5% drop over 24h
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 18

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Midpoint target of the recent 24-bar high/low range
            recent_high = max(highs[-self.lookback:])
            recent_low = min(lows[-self.lookback:])
            midpoint = (recent_high + recent_low) / 2.0
            
            # Exit conditions: Target midpoint reached OR time horizon expired
            if ctx.bar.close >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "exit_midpoint_reversion_reached",
                        "close": ctx.bar.close,
                        "midpoint": midpoint,
                        "bars_held": bars_held
                    }
                )
            
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_time_horizon_expired",
                        "close": ctx.bar.close,
                        "bars_held": bars_held
                    }
                )
            return None

        # Check cooldown post exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme system meltdowns
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Drop calculation: 24h peak-to-current drop
        prior_peak = max(highs[-self.lookback - 1 : -1])
        drop_pct = (ctx.bar.close - prior_peak) / prior_peak

        # Strict event boundary:
        # 1. 24-bar flush magnitude >= 5.5%
        # 2. Structural capitulation reversal: Close back above prior bar's high
        # 3. Bar closed bullish (Close > Open) indicating buying absorption
        is_flush = drop_pct <= self.flush_threshold_pct
        prev_high = highs[-2]
        is_structural_reversal = (ctx.bar.close > prev_high) and (ctx.bar.close > ctx.bar.open)

        if is_flush and is_structural_reversal:
            fg_index = ctx.features.get("fear_greed_index", 50)
            
            # Scale confidence when market fear aligns with panic drop
            confidence = 0.7
            if fg_index < 35:
                confidence = 0.85

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=43200,
                metadata={
                    "reason": "capitulation_break_above_prior_high",
                    "drop_pct": round(drop_pct * 100, 2),
                    "prev_high": prev_high,
                    "close": ctx.bar.close,
                    "fear_greed": fg_index,
                    "regime": market_regime
                }
            )

        return None