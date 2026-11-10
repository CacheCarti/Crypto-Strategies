from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityFadeEth(Strategy):
    METADATA = {
        "name": "Volatility Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar strategy, intended hold ~1 bar
        "warmup_bars": 50, # Need enough bars for ATR and Z-score
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.zscore_period = 20
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate first trade

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0 # Handle zero standard deviation
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Calculate ATR for volatility gauge
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None

        # Calculate Z-score for price deviation
        zscore = self._zscore(closes, self.zscore_period)
        if zscore is None:
            return None

        # Volatility condition: Only trade in top-quartile volatility (e.g., ATR > 75th percentile of recent ATRs)
        # For simplicity in this example, we'll use a fixed threshold or relative comparison.
        # A more robust approach would dynamically calculate percentiles of ATR.
        # Here, let's use a simple condition: current ATR is significantly higher than recent average ATR.
        
        # To avoid dynamic percentile calculation for brevity, we'll use a high absolute ATR value
        # or compare to a recent moving average of ATR if space allows.
        # For now, let's simplify to a high ATR value
        
        # A more robust approach would be to track recent ATRs and compare.
        # For this example, let's assume a simple threshold for "high volatility"
        # This will need to be tuned, but for ETH on 1h, 0.015 * price is a rough proxy for high vol.
        # As we don't have historical ATRs to percentile, we'll use a relative measure against average price.
        
        avg_price = sum(closes[-self.atr_period:]) / self.atr_period
        volatility_threshold = 0.015 * avg_price # e.g. 1.5% of average price
        
        is_high_volatility = atr > volatility_threshold

        # Regime filter: Do not fade calm trends. We want to fade extended moves, not strong trends.
        # If the market regime is "calm" or "normal", we might consider fading.
        # If it's "volatile" or "crisis", we might look for stronger fades or avoid.
        # For this strategy, we want to fade in high volatility, but only if it's not a strong trend.
        # Let's use the 'regime' for this. We'll fade in 'normal' or 'volatile' regimes, but not in 'crisis' or 'meltdown' (which implies strong, potentially unfadeable trends).
        
        current_regime = ctx.market.get("regime", "NORMAL")
        
        # We fade if the market is not in a strong crisis/meltdown, and is volatile.
        # "calm" regime might imply too little volatility to fade.
        can_fade_regime = current_regime in ["NORMAL", "HIGH_VOL"]

        # If already in a position, do nothing unless we want to flip or exit
        if ctx.has_position():
            return None

        # Entry Conditions for Fading Extended Moves
        # Long: Price is significantly oversold (low Z-score) in high volatility, not in strong trend
        if zscore < -2.0 and is_high_volatility and can_fade_regime:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_oversold_fade_high_vol",
                    "zscore": zscore,
                    "atr": atr,
                    "volatility_threshold": volatility_threshold,
                    "regime": current_regime,
                    "price": current_close
                }
            )

        # Short: Price is significantly overbought (high Z-score) in high volatility, not in strong trend
        elif zscore > 2.0 and is_high_volatility and can_fade_regime:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "zscore_overbought_fade_high_vol",
                    "zscore": zscore,
                    "atr": atr,
                    "volatility_threshold": volatility_threshold,
                    "regime": current_regime,
                    "price": current_close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already update last_trade_bar on entry. No further action needed here for cooldown.
        pass