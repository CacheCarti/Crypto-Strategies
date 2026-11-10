from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeAdaptive(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeAdaptive",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 14
        self.last_trade_bar = -999
        self.funding_history = []

    def _stddev(self, values: list) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        if len(closes) < self.lookback + 1:
            return None

        # Maintain rolling funding rates for dynamic dispersion/Z-score estimation
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > 120:
            self.funding_history.pop(0)

        # Respect execution cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Price momentum over lookback
        price_ret = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]

        # Dynamic funding context
        f_std = self._stddev(self.funding_history)
        # Avoid static thresholds: require funding sign persistent divergence
        # Long squeeze: Price gaining firmly while funding is persistently negative (shorts paying longs)
        # Short squeeze: Price dropping firmly while funding is persistently positive (longs paying shorts)
        price_threshold = 0.015  # 1.5% 24h displacement minimum
        funding_threshold = max(0.00005, f_std * 0.4)

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic based on momentum exhaustion or divergence resolution
        if has_pos:
            entry_price = ctx.position.get("entry_price", closes[-1])
            curr_ret_bps = ((closes[-1] - entry_price) / entry_price) * 10000.0 if pos_dir == "long" else ((entry_price - closes[-1]) / entry_price) * 10000.0
            
            # Trailing target exit if divergence normalized and in moderate profit
            if curr_ret_bps > 150.0:
                if pos_dir == "long" and funding >= funding_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_squeeze_normalized",
                        "funding": funding,
                        "ret_bps": curr_ret_bps
                    })
                elif pos_dir == "short" and funding <= -funding_threshold:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_squeeze_normalized",
                        "funding": funding,
                        "ret_bps": curr_ret_bps
                    })
            return None

        # Long Squeeze Entry
        if price_ret > price_threshold and funding < -funding_threshold:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.5 + abs(price_ret) * 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_funding_short_squeeze",
                    "price_ret_24": round(price_ret, 4),
                    "funding_rate": funding,
                    "f_std": round(f_std, 6)
                }
            )

        # Short Squeeze Entry (Long liquidation cascade)
        if price_ret < -price_threshold and funding > funding_threshold:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.5 + abs(price_ret) * 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_funding_long_squeeze",
                    "price_ret_24": round(price_ret, 4),
                    "funding_rate": funding,
                    "f_std": round(f_std, 6)
                }
            )

        return None