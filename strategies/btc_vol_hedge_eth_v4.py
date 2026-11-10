from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcVolHedgeEth(Strategy):
    METADATA = {
        "name": "BTC Vol Hedge ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60, # Need enough for ATR and SMA calculations
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20 # Period for realized volatility calculation
        self.vol_sma_period = 40 # Period for SMA of volatility
        self.vol_threshold_multiplier = 1.5 # How many multiples above SMA for "abnormal"
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _realized_volatility(self, closes, period):
        if len(closes) < period + 1:
            return None
        log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        if len(log_returns) < period:
            return None
        
        # Annualized volatility (assuming daily bars for simplicity, but here it's 1-hour)
        # For 1-hour, it's (sqrt(24*365)) * stddev. For relative comparison, stddev is enough.
        slice_returns = log_returns[-period:]
        mean_return = sum(slice_returns) / period
        variance = sum((x - mean_return) ** 2 for x in slice_returns) / period
        return math.sqrt(variance) * math.sqrt(252) # Annualized for comparison, but relative is key

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        btc_closes = ctx.market.get("btc_usdc_closes", [])
        if len(btc_closes) < self.warmup_bars:
            return None

        current_btc_vol = self._realized_volatility(btc_closes, self.vol_period)
        if current_btc_vol is None:
            return None

        # Calculate SMA of BTC volatility
        btc_vol_history = []
        for i in range(self.vol_period, len(btc_closes) + 1):
            vol = self._realized_volatility(btc_closes[:i], self.vol_period)
            if vol is not None:
                btc_vol_history.append(vol)

        if len(btc_vol_history) < self.vol_sma_period:
            return None

        sma_btc_vol = self._sma(btc_vol_history, self.vol_sma_period)
        if sma_btc_vol is None:
            return None

        # Check for abnormal BTC volatility expansion
        is_btc_vol_expanding_abnormally = current_btc_vol > (sma_btc_vol * self.vol_threshold_multiplier)

        # Get BTC and ETH return percentages
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Regime condition: BTC vol expanding abnormally AND BTC returns are negative
        is_regime_active = is_btc_vol_expanding_abnormally and btc_return_pct < -0.5 # BTC down by 0.5% or more

        if ctx.has_position():
            # If in a short position and regime is no longer active, consider exiting
            if ctx.position_direction() == "short" and not is_regime_active:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "vol_regime_normalized",
                        "current_btc_vol": current_btc_vol,
                        "sma_btc_vol": sma_btc_vol,
                        "btc_return_pct": btc_return_pct,
                        "eth_return_pct": eth_return_pct,
                    }
                )
            return None # Hold existing position if regime is still active

        # Entry logic: Only short ETH when the regime is active and ETH confirms weakness
        if is_regime_active and eth_return_pct < -0.5: # ETH also down by 0.5% or more
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_vol_expansion_eth_weakness",
                    "current_btc_vol": current_btc_vol,
                    "sma_btc_vol": sma_btc_vol,
                    "btc_return_pct": btc_return_pct,
                    "eth_return_pct": eth_return_pct,
                    "vol_threshold_multiplier": self.vol_threshold_multiplier,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or reset state
        pass