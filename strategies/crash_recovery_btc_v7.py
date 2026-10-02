from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class PanicRecoveryCapitulation(Strategy):
    METADATA = {
        "name": "Panic Recovery Capitulation",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,  # 4 hours
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.drop_pct = 0.020  # 2.0% flush from recent swing high
        self.cooldown_bars = 4
        self.max_hold_bars = 12
        self.cooldown_until = 0
        self.entry_bar = 0
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.cooldown_bars
        self.target_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 5:
            return None

        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar

            # Midpoint recovery target exit
            if self.target_price > 0.0 and current_price >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "midpoint_recovery_target_reached",
                        "price": round(current_price, 2),
                        "target_price": round(self.target_price, 2),
                        "bars_held": bars_held
                    }
                )

            # Max duration safety exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_hold_duration_reached",
                        "price": round(current_price, 2),
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until:
            return None

        # Skip extreme meltdown/crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        window_high = max(recent_highs)
        window_low = min(recent_lows)

        if window_high <= 0:
            return None

        drop = (window_high - current_price) / window_high
        rsi = self._rsi(closes, 14)

        # 1. Flush condition: Price dropped >= 2.0% from recent window high
        # 2. Capitulation reversal: Current close higher than open (green bounce candle) AND above previous close
        # 3. RSI check: not heavily overbought (RSI <= 55)
        is_flush = drop >= self.drop_pct
        is_reversal = (current_price > ctx.bar.open) and (current_price > closes[-2])
        is_favorable_rsi = (rsi is not None) and (rsi <= 55.0)

        if is_flush and is_reversal and is_favorable_rsi:
            midpoint = (window_high + window_low) / 2.0
            self.entry_bar = ctx.bar_index
            self.target_price = midpoint
            confidence = min(0.9, max(0.55, 0.5 + drop * 6.0))

            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_capitulation_bounce",
                    "drop_pct": round(drop * 100, 2),
                    "window_high": round(window_high, 2),
                    "window_low": round(window_low, 2),
                    "target_midpoint": round(midpoint, 2),
                    "rsi": round(rsi, 2) if rsi is not None else 0.0,
                    "price": round(current_price, 2)
                }
            )

        return None