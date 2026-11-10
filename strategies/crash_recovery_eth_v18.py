from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.045       # 4.5% drop over 24 bars (selective flush)
        self.cooldown_period = 8        # bars to wait after exit or signal
        self.last_trade_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.entry_price = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management: manual midpoint take-profit check
        if ctx.has_position():
            if ctx.position_direction() == "long":
                curr_price = ctx.bar.close
                # If target midpoint reached, exit flat
                target_mid = (self.flush_high + self.flush_low) / 2.0
                if target_mid > self.entry_price and curr_price >= target_mid:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "flush_midpoint_target_reached",
                            "price": curr_price,
                            "target_mid": target_mid,
                            "entry_price": self.entry_price,
                        }
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_period:
            return None

        # Regime gate to avoid non-recoverable meltdown / prolonged crisis failure
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("MELTDOWN", "CRISIS"):
            return None

        # Sentiment context filter: flushes recover best when sentiment reflects fear
        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed > 65:  # Skip euphoric tops or blow-off chop
            return None

        # Check 24-bar drop
        ref_price = closes[-self.lookback]
        curr_close = closes[-1]
        flush_return = (curr_close - ref_price) / ref_price

        if flush_return <= self.drop_thresh:
            prev_high = highs[-2]
            # Stabilization trigger: current close breaks above previous bar high
            if curr_close > prev_high:
                # Calculate flush range over lookback window
                window_highs = highs[-self.lookback:]
                window_lows = lows[-self.lookback:]
                f_high = max(window_highs)
                f_low = min(window_lows)
                midpoint = (f_high + f_low) / 2.0

                # Ensure reasonable upside remaining to midpoint
                expected_gain_bps = ((midpoint - curr_close) / curr_close) * 10000.0
                if expected_gain_bps >= 80.0:
                    rsi_val = self._rsi(closes, 14)
                    # Filter out already overbought bounces
                    if rsi_val < 60.0:
                        self.flush_high = f_high
                        self.flush_low = f_low
                        self.entry_price = curr_close
                        self.last_trade_bar = ctx.bar_index

                        # Compute dynamic TP based on distance to midpoint
                        calc_tp = max(150.0, min(400.0, expected_gain_bps))

                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=280.0,
                            take_profit_bps=calc_tp,
                            horizon_seconds=18000,
                            metadata={
                                "reason": "panic_recovery_stabilization",
                                "flush_return": round(flush_return, 4),
                                "rsi": round(rsi_val, 1),
                                "midpoint": round(midpoint, 2),
                                "expected_gain_bps": round(expected_gain_bps, 1),
                                "fear_greed": fear_greed,
                            }
                        )

        return None