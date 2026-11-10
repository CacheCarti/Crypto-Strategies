from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicRecoveryETH(Strategy):
    METADATA = {
        "name": "PanicRecoveryETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_price = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 2)
        highs = ctx.highs(self.lookback_bars + 2)
        lows = ctx.lows(self.lookback_bars + 2)

        if len(closes) < self.lookback_bars + 1:
            return None

        # Position management: evaluate mean-reversion midpoint target
        if ctx.has_position():
            curr_close = ctx.bar.close
            bars_in_trade = ctx.bar_index - self.entry_bar
            # Exit when price recovers to flush range midpoint or momentum stalls after 6+ bars
            if self.target_price > 0 and curr_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_hit",
                        "target_price": self.target_price,
                        "close": curr_close,
                        "bars_held": bars_in_trade,
                    }
                )
            if bars_in_trade >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_recovery_window_elapsed",
                        "close": curr_close,
                        "bars_held": bars_in_trade,
                    }
                )
            return None

        # Enforce cooldown after any exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Calculate flush magnitude over lookback
        recent_high = max(highs[-self.lookback_bars:])
        prev_bar_high = highs[-2]
        curr_close = closes[-1]
        curr_low = lows[-1]

        # Drawdown over the window
        drop_pct = (curr_close - recent_high) / recent_high

        # RSI filter: must be oversold or deeply depressed
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Check market crisis level to avoid catastrophic downward spirals
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50)

        # Trigger logic:
        # 1. Flush drop: drop >= 4.8% over lookback window
        # 2. Reversal/stabilization confirmation: current close > previous bar high
        # 3. Oversold context: RSI <= 38
        # 4. Fear context: avoid buying if market is completely neutral/euphoric
        is_flush = drop_pct <= -0.048
        is_stabilizing = curr_close > prev_bar_high
        is_oversold = rsi <= 38.0
        not_meltdown = crisis_score < 0.85
        fear_present = fg_index < 60

        if is_flush and is_stabilizing and is_oversold and not_meltdown and fear_present:
            recent_low = min(lows[-self.lookback_bars:])
            flush_midpoint = recent_low + (recent_high - recent_low) * 0.45
            self.target_price = flush_midpoint
            self.entry_bar = ctx.bar_index

            # Dynamic confidence based on magnitude of flush and oversold condition
            conf = min(0.9, max(0.6, abs(drop_pct) * 8.0 + (40.0 - rsi) / 100.0))

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=450.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "panic_recovery_stabilization",
                    "drop_pct": round(drop_pct * 100, 2),
                    "rsi": round(rsi, 2),
                    "prev_bar_high": prev_bar_high,
                    "target_midpoint": round(flush_midpoint, 2),
                    "fear_greed": fg_index,
                }
            )

        return None