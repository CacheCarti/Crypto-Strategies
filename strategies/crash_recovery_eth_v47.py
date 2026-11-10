from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -4.5
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_idx = ctx.bar_index
        current_close = ctx.bar.close
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Handle active position management
        if ctx.has_position():
            bars_in_trade = current_idx - self.entry_bar
            
            # Take profit near midpoint of the flush range
            if self.target_price > 0 and current_close >= self.target_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "target_price": self.target_price,
                    "close": current_close,
                    "bars_in_trade": bars_in_trade
                })
            
            # Time-based exhaustion exit (max 12 bars ~ 12h)
            if bars_in_trade >= 12:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "time_exhaustion_exit",
                    "close": current_close,
                    "bars_in_trade": bars_in_trade
                })

            return None

        # Check cooldown
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate 24-bar drop
        past_close = closes[-(self.lookback + 1)]
        cum_return_pct = ((current_close - past_close) / past_close) * 100.0

        # RSI filter: must be oversold/depressed to reflect real panic
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None:
            return None

        # Flush condition: drop <= -4.5%, RSI < 38, not in extreme greed (>75)
        is_flush = (cum_return_pct <= self.flush_threshold_pct) and (rsi_val < 38.0) and (fg_index <= 75.0)

        if not is_flush:
            return None

        # Stabilization confirmation: current bar closes above previous bar's high
        prev_high = highs[-2]
        if current_close > prev_high:
            # Measure recent flush range over lookback
            recent_high = max(highs[-self.lookback:])
            recent_low = min(lows[-self.lookback:])
            
            # Midpoint target of the flush range
            midpoint = (recent_high + recent_low) / 2.0
            
            # Ensure target offers a favorable recovery buffer (> 70 bps above current)
            if midpoint > current_close * 1.007:
                self.flush_high = recent_high
                self.flush_low = recent_low
                self.target_price = midpoint
                self.entry_bar = current_idx

                # Scale confidence slightly by severity of flush
                conf = min(0.9, 0.65 + abs(cum_return_pct) * 0.02)

                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "panic_flush_stabilization_reversal",
                        "cum_return_pct": cum_return_pct,
                        "rsi": rsi_val,
                        "target_price": midpoint,
                        "fear_greed": fg_index,
                        "close": current_close,
                        "prev_high": prev_high
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0