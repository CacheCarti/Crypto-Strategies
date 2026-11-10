from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "SolPanicFlushRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 43200,  # ~12 hours swing target
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.07  # -7% flush over 24h
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.active_flush_high = 0.0
        self.active_flush_low = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.active_flush_high = 0.0
        self.active_flush_low = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        # Position management
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit near flush-range midpoint if recorded
                if self.active_flush_high > 0 and self.active_flush_low > 0:
                    midpoint = (self.active_flush_high + self.active_flush_low) / 2.0
                    if current_close >= midpoint:
                        return ctx.signal(
                            "flat",
                            confidence=0.8,
                            metadata={
                                "reason": "flush_midpoint_reversion_target_hit",
                                "midpoint": round(midpoint, 2),
                                "close": round(current_close, 2),
                                "flush_high": round(self.active_flush_high, 2),
                                "flush_low": round(self.active_flush_low, 2)
                            }
                        )
            return None

        # Cooldown guard after trade exit
        if (current_bar - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries in unhedged meltdown regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # 24-bar drop calculation (event boundary)
        ref_close = closes[- (self.lookback + 1)]
        prev_close = closes[-2]
        rolling_return = (prev_close - ref_close) / ref_close

        # Strict event boundary: Flush condition must be satisfied
        if rolling_return <= self.flush_threshold_pct:
            recent_highs = highs[-(self.lookback + 1):-1]
            recent_lows = lows[-(self.lookback + 1):-1]
            flush_high = max(recent_highs)
            flush_low = min(recent_lows)

            prev_high = highs[-2]
            
            # Stabilization trigger: current bar closes strictly above the previous bar's high
            if current_close > prev_high:
                # Calculate stop-loss under the panic low
                raw_risk = current_close - flush_low
                if raw_risk <= 0:
                    sl_bps = 350.0
                else:
                    sl_bps = min(max((raw_risk / current_close) * 10000.0, 250.0), 650.0)

                # Midpoint take-profit target in bps
                midpoint = (flush_high + flush_low) / 2.0
                if midpoint > current_close:
                    tp_bps = min(max(((midpoint - current_close) / current_close) * 10000.0, 300.0), 900.0)
                else:
                    tp_bps = sl_bps * 1.5

                self.active_flush_high = flush_high
                self.active_flush_low = flush_low

                # Sentiment adjustment to confidence
                fgi = ctx.features.get("fear_greed_index", 50)
                confidence = 0.85 if fgi < 35 else 0.70

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=round(sl_bps, 1),
                    take_profit_bps=round(tp_bps, 1),
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_panic_stabilization_reversal",
                        "rolling_24h_return": round(rolling_return, 4),
                        "prev_high": round(prev_high, 2),
                        "close": round(current_close, 2),
                        "panic_low": round(flush_low, 2),
                        "flush_high": round(flush_high, 2),
                        "sl_bps": round(sl_bps, 1),
                        "tp_bps": round(tp_bps, 1),
                        "fgi": fgi
                    }
                )

        return None