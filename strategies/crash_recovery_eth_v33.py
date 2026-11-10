from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target recovery horizon
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.bars_in_trade = 0
        self.max_hold_bars = 18
        self.entry_price = 0.0
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

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
        self.bars_in_trade = 0
        self.entry_price = 0.0
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position Management
        if ctx.has_position():
            self.bars_in_trade += 1
            curr_close = ctx.bar.close

            # Target exit: reaching the midpoint of the flush range
            if self.target_price > 0 and curr_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "close": curr_close,
                        "target": self.target_price,
                        "bars_held": self.bars_in_trade,
                    },
                )

            # Volatility / Structural breakdown stop: bar closes below the flush low
            if self.flush_low > 0 and curr_close < self.flush_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "structural_flush_low_broken",
                        "close": curr_close,
                        "flush_low": self.flush_low,
                    },
                )

            # Timed thesis expiration
            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_time_expired",
                        "bars_held": self.bars_in_trade,
                        "close": curr_close,
                    },
                )

            return None

        # Entry Eligibility & Cooldown Check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime / Crisis Check: avoid pure meltdown liquidity traps
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        flush_high = max(recent_highs)
        flush_low = min(recent_lows)

        # Drop of 5%+ over the lookback window
        drop_pct = (flush_high - ctx.bar.close) / flush_high
        if drop_pct < 0.05:
            return None

        # Stabilization trigger: current close breaks strictly above previous bar's high
        prev_high = highs[-2]
        if ctx.bar.close <= prev_high:
            return None

        # Ensure bar is constructive (bullish close)
        if ctx.bar.close <= ctx.bar.open:
            return None

        # Calculate Flush Midpoint Target
        flush_midpoint = (flush_high + flush_low) / 2.0
        # If price already exceeded midpoint, recovery setup has passed
        if ctx.bar.close >= flush_midpoint:
            return None

        # ATR-based volatility structural check
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None:
            return None

        dist_to_low = ctx.bar.close - flush_low
        sl_bps = max(150.0, min(500.0, (dist_to_low / ctx.bar.close) * 10000.0 + 30.0))
        dist_to_target = flush_midpoint - ctx.bar.close
        tp_bps = max(150.0, min(650.0, (dist_to_target / ctx.bar.close) * 10000.0))

        # Store trade state
        self.entry_price = ctx.bar.close
        self.flush_high = flush_high
        self.flush_low = flush_low
        self.target_price = flush_midpoint
        self.bars_in_trade = 0

        # Sentiment tilt for confidence
        fgi = ctx.features.get("fear_greed_index", 50.0)
        confidence = 0.75 if fgi < 40 else 0.65

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "flush_5pct_stabilization_long",
                "drop_pct": round(drop_pct * 100, 2),
                "flush_high": flush_high,
                "flush_low": flush_low,
                "flush_midpoint": flush_midpoint,
                "prev_high": prev_high,
                "close": ctx.bar.close,
                "atr": round(atr, 2),
                "fear_greed": fgi,
            },
        )