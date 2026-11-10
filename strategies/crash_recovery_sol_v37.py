from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "SolPanicFlushRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.07  # -7% drop over 24h
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.bars_in_trade = 0

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_close = ctx.bar.close
        ref_close = closes[-(self.lookback + 1)]
        prev_high = highs[-2]

        # In-position management
        if ctx.has_position():
            self.bars_in_trade += 1
            entry_price = ctx.position.get("entry_price", current_close) if ctx.position else current_close
            panic_low = min(lows[-(self.lookback):])
            panic_high = max(highs[-(self.lookback):])
            midpoint = (panic_high + panic_low) / 2.0

            # Dynamic midpoint target reached or time-stop (24 bars max)
            if current_close >= midpoint and current_close > entry_price:
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "midpoint": round(midpoint, 2),
                    "close": current_close,
                    "bars_held": self.bars_in_trade
                })

            if self.bars_in_trade >= 24:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_exhaustion_flat",
                    "bars_held": self.bars_in_trade,
                    "close": current_close
                })

            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gate to address prior failure (oos_reject from cascading meltdown)
        # Avoid unconstrained meltdowns / high crisis where cascade doesn't mean-revert
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        if market_regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Price-based flush condition: 24-bar return <= -7%
        rolling_return = (current_close - ref_close) / ref_close
        if rolling_return > self.drop_thresh:
            return None

        # Reversal stabilization trigger: current close breaks above previous bar high
        if current_close <= prev_high:
            return None

        # Momentum filter: RSI oversold to neutral bounce (not already hyper-extended)
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 55.0:
            return None

        # Calculate structure levels
        panic_low = min(lows[-self.lookback:])
        panic_high = max(highs[-self.lookback:])
        midpoint = (panic_high + panic_low) / 2.0

        # Structural stop: place below panic low + cushion
        calc_sl_bps = max(180.0, min(500.0, ((current_close - panic_low) / current_close) * 10000.0 + 40.0))
        # Structural take-profit: toward the range midpoint
        midpoint_diff_bps = ((midpoint - current_close) / current_close) * 10000.0
        calc_tp_bps = max(250.0, min(650.0, midpoint_diff_bps))

        # Size confidence based on panic magnitude and sentiment washout
        conf = 0.65
        if fg_index < 30.0:
            conf += 0.15
        if rolling_return < -0.10:
            conf += 0.10
        conf = min(0.95, max(0.5, conf))

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=calc_sl_bps,
            take_profit_bps=calc_tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sol_panic_stabilization_break_prev_high",
                "rolling_return_24h": round(rolling_return, 4),
                "rsi": round(rsi_val, 2) if rsi_val else 0.0,
                "panic_low": round(panic_low, 2),
                "panic_high": round(panic_high, 2),
                "midpoint": round(midpoint, 2),
                "fear_greed": fg_index,
                "regime": market_regime
            }
        )