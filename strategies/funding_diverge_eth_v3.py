from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingDivergenceETH(Strategy):
    METADATA = {
        "name": "Funding Divergence ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # ~3 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 2  # Lookback for funding rate change
        self.price_change_period = 3 # Lookback for price change
        self.funding_threshold = -0.00005  # Negative funding rate threshold for long signal
        self.funding_positive_threshold = 0.00005 # Positive funding rate threshold for short signal
        self.price_change_threshold = 0.001 # 0.1% price change
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store previous funding rate state to detect transitions
        self.prev_funding_rate = None
        self.prev_funding_rate_long_signal = False
        self.prev_funding_rate_short_signal = False


    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_change_period + 1)
        funding_rates = [ctx.features.get("funding_rate_ethusdt", 0.0) for _ in range(self.funding_period + 1)]

        if len(closes) < self.price_change_period + 1 or len(funding_rates) < self.funding_period + 1:
            return None

        if ctx.bar_index < self.warmup_bars:
            return None

        current_funding_rate = funding_rates[-1]
        prev_funding_rate_avg = sum(funding_rates[:-1]) / self.funding_period if self.funding_period > 0 else funding_rates[-1]

        # Calculate recent price change
        price_change_pct = (closes[-1] - closes[-(self.price_change_period)]) / closes[-(self.price_change_period)]

        # --- Long Signal: Rising Price with Negative Funding (short squeeze potential) ---
        long_condition_funding = current_funding_rate < self.funding_threshold and current_funding_rate < prev_funding_rate_avg
        long_condition_price = price_change_pct > self.price_change_threshold

        # --- Short Signal: Falling Price with Positive Funding (long liquidation potential) ---
        short_condition_funding = current_funding_rate > self.funding_positive_threshold and current_funding_rate > prev_funding_rate_avg
        short_condition_price = price_change_pct < -self.price_change_threshold


        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        if ctx.has_position():
            # Check for conditions to exit current position
            if ctx.position_direction() == "long":
                # Exit long if funding turns positive or price starts falling significantly
                if current_funding_rate > 0 or price_change_pct < -self.price_change_threshold * 1.5:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_exit_funding_positive_or_price_fall",
                        "current_funding_rate": current_funding_rate,
                        "price_change_pct": price_change_pct
                    })
            elif ctx.position_direction() == "short":
                # Exit short if funding turns negative or price starts rising significantly
                if current_funding_rate < 0 or price_change_pct > self.price_change_threshold * 1.5:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_exit_funding_negative_or_price_rise",
                        "current_funding_rate": current_funding_rate,
                        "price_change_pct": price_change_pct
                    })
            return None # Hold current position

        # Entry logic
        signal_confidence = 0.7

        if long_condition_funding and long_condition_price:
            # Only trigger if funding condition just transitioned to meet criteria
            if not self.prev_funding_rate_long_signal:
                self.last_trade_bar = ctx.bar_index
                self.prev_funding_rate_long_signal = True
                self.prev_funding_rate_short_signal = False # Reset other
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "funding_divergence_long_squeeze",
                        "current_funding_rate": current_funding_rate,
                        "prev_funding_rate_avg": prev_funding_rate_avg,
                        "price_change_pct": price_change_pct,
                    }
                )
        elif short_condition_funding and short_condition_price:
            # Only trigger if funding condition just transitioned to meet criteria
            if not self.prev_funding_rate_short_signal:
                self.last_trade_bar = ctx.bar_index
                self.prev_funding_rate_short_signal = True
                self.prev_funding_rate_long_signal = False # Reset other
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "funding_divergence_short_liquidation",
                        "current_funding_rate": current_funding_rate,
                        "prev_funding_rate_avg": prev_funding_rate_avg,
                        "price_change_pct": price_change_pct,
                    }
                )
        else:
            # Reset transition flags if conditions are no longer met
            self.prev_funding_rate_long_signal = False
            self.prev_funding_rate_short_signal = False

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not used for adaptive logic in this strategy, but available.
        pass