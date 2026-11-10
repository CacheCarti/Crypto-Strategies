from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.max_channel_width_pct = 0.018  # Channel width must be < 1.8% to qualify as chop
        self.min_channel_width_pct = 0.005  # Reject completely dead illiquid tape
        self.fade_threshold = 0.12          # Within 12% of the boundary
        self.cooldown_bars = 16             # Enforce ~80 min cooldown after trade
        self.last_trade_bar = -100

    def _stoch_rsi(self, closes: list, rsi_period: int = 14, stoch_period: int = 14) -> Optional[float]:
        total_len = rsi_period + stoch_period
        if len(closes) < total_len + 1:
            return None
        
        # Calculate fast rolling RSI values
        rsi_values = []
        for end_idx in range(len(closes) - stoch_period, len(closes) + 1):
            sub = closes[:end_idx]
            gains, losses = 0.0, 0.0
            for i in range(len(sub) - rsi_period, len(sub)):
                diff = sub[i] - sub[i - 1]
                if diff > 0:
                    gains += diff
                else:
                    losses -= diff
            avg_gain = gains / rsi_period
            avg_loss = losses / rsi_period
            if avg_loss == 0.0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))
            rsi_values.append(rsi)

        lowest_rsi = min(rsi_values)
        highest_rsi = max(rsi_values)
        if highest_rsi == lowest_rsi:
            return 50.0
        return ((rsi_values[-1] - lowest_rsi) / (highest_rsi - lowest_rsi)) * 100.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 20)
        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)

        if len(closes) < self.channel_period + 20 or len(highs) < self.channel_period:
            return None

        # Cross-asset macro regime check (avoid range fading when BTC is moving violently)
        btc_ret_24h = abs(ctx.features.get("btc_return_pct", 0.0))
        if btc_ret_24h > 4.5:
            return None

        # Channel calculation
        c_high = max(highs)
        c_low = min(lows)
        if c_low <= 0:
            return None

        width = c_high - c_low
        midline = (c_high + c_low) / 2.0
        width_pct = width / midline
        current_close = closes[-1]

        # In-position management: take profit / flatten near midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "midline_reached_long_exit",
                    "price": current_close,
                    "midline": midline,
                    "channel_width_pct": width_pct
                })
            elif pos_dir == "short" and current_close <= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "midline_reached_short_exit",
                    "price": current_close,
                    "midline": midline,
                    "channel_width_pct": width_pct
                })
            return None

        # Cooldown guard: strictly limit trade frequency to protect against friction
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter: reject crisis / high-vol environments
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        # Stand down if channel is too wide (breakout/trend) or too dead
        if width_pct > self.max_channel_width_pct or width_pct < self.min_channel_width_pct:
            return None

        # Stoch RSI confirmation to confirm reversal momentum
        stoch_k = self._stoch_rsi(closes, rsi_period=14, stoch_period=14)
        if stoch_k is None:
            return None

        # Channel position: 0.0 = at low, 1.0 = at high
        relative_pos = (current_close - c_low) / width if width > 0 else 0.5

        # Fade lower edge: long setup
        if relative_pos <= self.fade_threshold and stoch_k <= 18.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_low_stoch_oversold",
                    "channel_low": c_low,
                    "channel_high": c_high,
                    "width_pct": width_pct,
                    "stoch_k": stoch_k,
                    "btc_ret_24h": btc_ret_24h,
                    "price": current_close,
                }
            )

        # Fade upper edge: short setup
        if relative_pos >= (1.0 - self.fade_threshold) and stoch_k >= 82.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_high_stoch_overbought",
                    "channel_low": c_low,
                    "channel_high": c_high,
                    "width_pct": width_pct,
                    "stoch_k": stoch_k,
                    "btc_ret_24h": btc_ret_24h,
                    "price": current_close,
                }
            )

        return None