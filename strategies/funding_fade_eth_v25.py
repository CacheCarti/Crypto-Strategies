from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.consecutive_crowd_bars = 0
        self.last_crowd_bias = 0  # 1 for overleveraged longs, -1 for shorts
        self.funding_long_thresh = -0.00015   # <= -0.015% per 8h
        self.funding_short_thresh = 0.00030   # >= 0.030% per 8h

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup)
        highs = ctx.highs(self.warmup)
        lows = ctx.lows(self.warmup)

        if len(closes) < self.warmup:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # Track persistent crowd positioning
        current_bias = 0
        if funding <= self.funding_long_thresh:
            current_bias = -1  # crowd is aggressive short -> squeeze long setup
        elif funding >= self.funding_short_thresh:
            current_bias = 1   # crowd is aggressive long -> squeeze short setup

        if current_bias != 0 and current_bias == self.last_crowd_bias:
            self.consecutive_crowd_bars += 1
        else:
            self.consecutive_crowd_bars = 1 if current_bias != 0 else 0
            self.last_crowd_bias = current_bias

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Dynamic normalization exit: exit when funding normalizes towards balance
            if pos_dir == "long" and funding >= -0.00002:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_exit_long",
                    "funding": funding,
                    "close": ctx.bar.close
                })
            elif pos_dir == "short" and funding <= 0.00005:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_exit_short",
                    "funding": funding,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme unmodeled meltdown regimes
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # Persistence requirement: crowd skew must last at least 3 bars
        if self.consecutive_crowd_bars < 3:
            return None

        sma20 = self._sma(closes, 20)
        atr14 = self._atr(highs, lows, closes, 14)
        if sma20 is None or atr14 is None or atr14 == 0:
            return None

        curr_close = ctx.bar.close

        # Volatility stretch filter: ensure we aren't fighting an active runaway trend
        stretch = (curr_close - sma20) / atr14

        # Entry 1: Crowd heavily short (funding negative) + Cross-asset stable/supportive + Price not in freefall
        if self.last_crowd_bias == -1:
            if btc_ret > -2.0 and stretch > -2.2:
                # Modest price turn confirmation: close above recent 3-bar low
                recent_low = min(lows[-4:-1])
                if curr_close > recent_low:
                    conf = min(0.9, 0.65 + abs(funding) * 1000.0)
                    return ctx.signal(
                        "long",
                        confidence=round(conf, 2),
                        stop_loss_bps=280.0,
                        take_profit_bps=450.0,
                        horizon_seconds=14400,
                        metadata={
                            "reason": "contrarian_short_crowd_squeeze",
                            "funding": funding,
                            "persistence_bars": self.consecutive_crowd_bars,
                            "btc_return_pct": btc_ret,
                            "stretch": round(stretch, 2),
                            "close": curr_close
                        }
                    )

        # Entry 2: Crowd heavily long (funding elevated) + Cross-asset neutral/cautious + Price not vertically parabolic
        elif self.last_crowd_bias == 1:
            if btc_ret < 2.5 and stretch < 2.2:
                # Price hesitation confirmation: close below recent 3-bar high
                recent_high = max(highs[-4:-1])
                if curr_close < recent_high:
                    conf = min(0.9, 0.65 + funding * 1000.0)
                    return ctx.signal(
                        "short",
                        confidence=round(conf, 2),
                        stop_loss_bps=280.0,
                        take_profit_bps=450.0,
                        horizon_seconds=14400,
                        metadata={
                            "reason": "contrarian_long_crowd_fade",
                            "funding": funding,
                            "persistence_bars": self.consecutive_crowd_bars,
                            "btc_return_pct": btc_ret,
                            "stretch": round(stretch, 2),
                            "close": curr_close
                        }
                    )

        return None