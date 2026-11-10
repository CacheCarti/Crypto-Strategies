from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,  # ~5 bars (25 mins)
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.max_hold_bars = 5
        self.bars_since_exit = 20
        self.bars_in_trade = 0
        self.btc_ret_history = []
        self.min_btc_spike = 1.6
        self.sol_lag_threshold = 0.65

    def _sol_return(self, closes, lookback=4) -> float:
        if len(closes) < lookback + 1:
            return 0.0
        return ((closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback]) * 100.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(btc_ret)
        if len(self.btc_ret_history) > 10:
            self.btc_ret_history.pop(0)

        # Track hold duration and exit if maximum scalp horizon reached
        if ctx.has_position():
            self.bars_in_trade += 1
            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "max_hold_bars_reached",
                    "bars_in_trade": self.bars_in_trade,
                    "close": ctx.bar.close
                })
            return None

        self.bars_since_exit += 1
        if self.bars_since_exit < self.cooldown_bars:
            return None

        # Filter out crisis and extreme turbulence where cross-asset beta lags decouple unpredictably
        regime = ctx.regime.lower()
        if regime in ("crisis", "volatile"):
            return None

        market_regime = ctx.market.get("regime", "NORMAL").upper()
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        if len(self.btc_ret_history) < 4:
            return None

        # Freshness: BTC 24h return shifted substantially within the last 3 bars
        btc_shift_3bars = btc_ret - self.btc_ret_history[-4]
        sol_ret_3bars = self._sol_return(closes, lookback=3)

        # Long: BTC moved sharply upward, but SOL hasn't caught up yet
        if btc_shift_3bars >= self.min_btc_spike and sol_ret_3bars <= self.sol_lag_threshold:
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_bullish_sol_lag",
                    "btc_shift": round(btc_shift_3bars, 3),
                    "sol_ret": round(sol_ret_3bars, 3),
                    "price": ctx.bar.close
                }
            )

        # Short: BTC dropped sharply, but SOL has remained buoyant
        if btc_shift_3bars <= -self.min_btc_spike and sol_ret_3bars >= -self.sol_lag_threshold:
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_bearish_sol_lag",
                    "btc_shift": round(btc_shift_3bars, 3),
                    "sol_ret": round(sol_ret_3bars, 3),
                    "price": ctx.bar.close
                }
            )

        return None