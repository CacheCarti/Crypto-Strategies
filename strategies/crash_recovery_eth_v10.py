from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "ETH Panic Flush Recovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold for swing mean reversion
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = -0.045     # 4.5% drop over 24h to capture genuine flushes
        self.cooldown_bars = 14          # Mandatory spacing between setups
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_midpoint = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        prev_high = highs[-2]
        
        # In-position management: take profit near the flush range midpoint or dynamic timeout
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Midpoint target reached
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_target_hit",
                    "target_midpoint": round(self.target_midpoint, 2),
                    "close": current_close,
                    "bars_held": bars_held
                })
            # Time-based expiration if mean-reversion stalls after 16 bars
            if bars_held >= 16:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "max_hold_time_reached",
                    "close": current_close,
                    "bars_held": bars_held
                })
            return None

        # Check post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Economic flush detection: 24-bar drop
        ref_close = closes[-(self.lookback + 1)]
        eth_drop_24h = (current_close - ref_close) / ref_close

        if eth_drop_24h <= self.drop_threshold:
            # Materially different confirmation:
            # 1. Price stabilization: close crosses above previous bar high
            stabilized = current_close > prev_high
            
            # 2. Cross-asset relative resilience: ETH is not lagging BTC precipitously
            btc_ret = ctx.features.get("btc_return_pct", 0.0)
            cross_asset_ok = (eth_drop_24h * 100.0) >= (btc_ret - 6.0)

            # 3. Volatility expansion filter: ATR must be elevated indicating the selling climax is active
            atr = self._atr(highs, lows, closes, 14)
            avg_close = sum(closes[-14:]) / 14
            rel_atr = (atr / avg_close) if (atr and avg_close > 0) else 0.0
            vol_expansion = rel_atr >= 0.008  # At least 80 bps normalized hourly range

            if stabilized and cross_asset_ok and vol_expansion:
                recent_highs = highs[-(self.lookback + 1):]
                recent_lows = lows[-(self.lookback + 1):]
                self.flush_high = max(recent_highs)
                self.flush_low = min(recent_lows)
                self.target_midpoint = (self.flush_high + self.flush_low) / 2.0

                # Ensure there is sufficient room to the midpoint to beat friction (> 120 bps)
                expected_gain_bps = ((self.target_midpoint - current_close) / current_close) * 10000.0
                if expected_gain_bps >= 120.0:
                    self.entry_bar = ctx.bar_index
                    fgi = ctx.features.get("fear_greed_index", 50)
                    confidence = 0.75 if fgi < 40 else 0.65

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=350.0,
                        take_profit_bps=max(250.0, min(expected_gain_bps, 600.0)),
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "panic_flush_reversal_confirmed",
                            "eth_drop_24h": round(eth_drop_24h, 4),
                            "btc_return_pct": round(btc_ret, 2),
                            "target_midpoint": round(self.target_midpoint, 2),
                            "expected_gain_bps": round(expected_gain_bps, 1),
                            "rel_atr": round(rel_atr, 4)
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0