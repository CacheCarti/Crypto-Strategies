from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0
        self.flush_low = 0.0
        self.max_hold_bars = 20

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0
        self.flush_low = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_bar_idx = ctx.bar_index

        # Position Management
        if ctx.has_position():
            bars_held = current_bar_idx - self.entry_bar
            
            # Midpoint target reached or time exit
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_reached",
                        "price": current_close,
                        "target_midpoint": round(self.target_midpoint, 2),
                        "bars_held": bars_held
                    }
                )

            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_timeout",
                        "price": current_close,
                        "bars_held": bars_held
                    }
                )

            # Re-break below flush low
            if self.flush_low > 0.0 and current_close < self.flush_low:
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "flush_low_violation",
                        "price": current_close,
                        "flush_low": round(self.flush_low, 2),
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard
        if (current_bar_idx - self.last_exit_bar) < self.cooldown_period:
            return None

        # Market regime filter: avoid outright crisis meltdowns where knife catching is fatal
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.75:
            return None

        # Check for 5%+ drop over the trailing 24 bars
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        peak_high = max(window_highs)
        trough_low = min(window_lows)

        if peak_high <= 0:
            return None

        drop_pct = (peak_high - current_close) / peak_high

        # 5% to 15% drop qualifies (capping prevents catastrophic unwinds/insolvency events)
        if 0.048 <= drop_pct <= 0.16:
            prev_high = highs[-2]
            
            # Stabilization trigger: close above previous bar's high
            if current_close > prev_high:
                # RSI check to ensure we are rebounding from depressed conditions (not grinding sideways)
                rsi_val = self._rsi(closes, 14)
                if rsi_val is not None and rsi_val < 48.0:
                    midpoint = (peak_high + trough_low) / 2.0
                    
                    # Ensure upside to midpoint justifies friction & risk
                    expected_gain_bps = ((midpoint - current_close) / current_close) * 10000.0
                    if expected_gain_bps >= 120.0:
                        self.target_midpoint = midpoint
                        self.flush_low = trough_low
                        self.entry_bar = current_bar_idx

                        confidence = min(0.85, 0.55 + (drop_pct * 2.0))

                        return ctx.signal(
                            "long",
                            confidence=round(confidence, 2),
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "panic_flush_stabilization_long",
                                "drop_pct": round(drop_pct * 100, 2),
                                "rsi": round(rsi_val, 2),
                                "peak_high": round(peak_high, 2),
                                "trough_low": round(trough_low, 2),
                                "target_midpoint": round(midpoint, 2),
                                "expected_gain_bps": round(expected_gain_bps, 1)
                            }
                        )

        return None