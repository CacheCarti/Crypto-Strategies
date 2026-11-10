from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirm(Strategy):
    METADATA = {
        "name": "CrossAssetTrendConfirm_SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars - 1 # Allow trading on first bar after warmup
        self.min_btc_return = 0.005 # 0.5%
        self.min_sol_return = 0.003 # 0.3%
        self.max_divergence_threshold = 0.002 # BTC and SOL returns diverge by more than this
        self.max_hold_bars = self.METADATA["declared_hold_seconds"] // 3600 + 1 # Convert seconds to bars for 1h domain
        self.position_entry_bar = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(-1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Indicators for SOL
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if ema_fast is None or ema_slow is None:
            return None

        # Cross-asset data
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        
        # Regime filters
        market_regime = ctx.market.get("trend_regime", "neutral")
        market_regime_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        volatility_regime = ctx.market.get("regime", "NORMAL")

        # Exit logic
        if ctx.has_position():
            if ctx.bar_index - self.position_entry_bar > self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_stop_hit", "bars_held": ctx.bar_index - self.position_entry_bar})
            
            # Check for normalization of relationship or reversal
            sol_return_1h = (closes[-1] / closes[-2] - 1) if len(closes) >= 2 else 0.0
            
            if ctx.position_direction() == "long":
                # Exit long if BTC turns negative or SOL momentum fades significantly or divergence is too high
                if btc_return_pct < -0.001 or sol_return_1h < -0.001 or (btc_return_pct > 0 and sol_return_1h > 0 and abs(btc_return_pct - sol_return_1h) > self.max_divergence_threshold):
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_exit_btc_negative_or_sol_fading_or_divergence", "btc_return_pct": btc_return_pct, "sol_return_1h": sol_return_1h})
                # If EMA cross down occurs while in long position
                if ema_fast < ema_slow and closes[-1] < ema_slow:
                    return ctx.signal("flat", confidence=0.75, metadata={"reason": "long_exit_ema_cross_down", "ema_fast": ema_fast, "ema_slow": ema_slow})
            elif ctx.position_direction() == "short":
                # Exit short if BTC turns positive or SOL momentum recovers significantly or divergence is too high
                if btc_return_pct > 0.001 or sol_return_1h > 0.001 or (btc_return_pct < 0 and sol_return_1h < 0 and abs(btc_return_pct - sol_return_1h) > self.max_divergence_threshold):
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_exit_btc_positive_or_sol_recovering_or_divergence", "btc_return_pct": btc_return_pct, "sol_return_1h": sol_return_1h})
                # If EMA cross up occurs while in short position
                if ema_fast > ema_slow and closes[-1] > ema_slow:
                    return ctx.signal("flat", confidence=0.75, metadata={"reason": "short_exit_ema_cross_up", "ema_fast": ema_fast, "ema_slow": ema_slow})
            return None # Hold position

        # Entry logic
        sol_return_1h = (closes[-1] / closes[-2] - 1) if len(closes) >= 2 else 0.0

        # Long conditions
        if (btc_return_pct > self.min_btc_return and
            sol_return_1h > self.min_sol_return and
            ema_fast > ema_slow and
            current_price > ema_fast and
            market_regime == "bull" and market_regime_confidence > 0.6 and
            volatility_regime in ["NORMAL", "HIGH_VOL"]):
            
            self.last_trade_bar = ctx.bar_index
            self.position_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                metadata={
                    "reason": "cross_asset_bull_confirm",
                    "btc_return_pct": btc_return_pct,
                    "sol_return_1h": sol_return_1h,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "market_regime": market_regime,
                    "market_regime_confidence": market_regime_confidence,
                    "volatility_regime": volatility_regime,
                }
            )

        # Short conditions
        if (btc_return_pct < -self.min_btc_return and
            sol_return_1h < -self.min_sol_return and
            ema_fast < ema_slow and
            current_price < ema_fast and
            market_regime == "bear" and market_regime_confidence > 0.6 and
            volatility_regime in ["NORMAL", "HIGH_VOL"]):
            
            self.last_trade_bar = ctx.bar_index
            self.position_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                metadata={
                    "reason": "cross_asset_bear_confirm",
                    "btc_return_pct": btc_return_pct,
                    "sol_return_1h": sol_return_1h,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "market_regime": market_regime,
                    "market_regime_confidence": market_regime_confidence,
                    "volatility_regime": volatility_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown after any close
        self.last_trade_bar = ctx.bar_index
        self.position_entry_bar = -1