from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # 6 hours
        "warmup_bars": 200,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 168
        self.channel_len = 12
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.was_spiking = False
        self.coiled = False
        self.coil_bars_left = 0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def _realized_vol(self, closes: list, lookback: int = 12) -> float:
        if len(closes) < lookback + 1:
            return 0.0
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - lookback, len(closes))]
        mean_ret = sum(rets) / len(rets)
        var = sum((r - mean_ret) ** 2 for r in rets) / len(rets)
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_window + 20)
        highs = ctx.highs(self.channel_len + 2)
        lows = ctx.lows(self.channel_len + 2)

        if len(closes) < self.vol_window + 15 or len(highs) < self.channel_len + 2:
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate current short-term realized volatility (12-bar)
        curr_vol = self._realized_vol(closes, lookback=12)

        # Build rolling distribution of short-term realized vol over last 168 bars
        vol_hist = []
        for i in range(12, self.vol_window + 12):
            sub_closes = closes[-i - 12 : -i]
            if len(sub_closes) >= 13:
                vol_hist.append(self._realized_vol(sub_closes, lookback=12))

        if len(vol_hist) < 50:
            return None

        median_vol = statistics.median(vol_hist)
        if median_vol <= 0.0:
            return None

        vol_ratio = curr_vol / median_vol

        # Detect volatility transition: spike (>1.8x median) then collapse back (<1.0x median)
        if vol_ratio > 1.8:
            self.was_spiking = True
        elif self.was_spiking and vol_ratio < 1.0:
            self.coiled = True
            self.was_spiking = False
            self.coil_bars_left = 18  # Window of opportunity for coil release

        if self.coil_bars_left > 0:
            self.coil_bars_left -= 1
        else:
            self.coiled = False

        if ctx.has_position():
            return None

        # Only trigger entry on active coil state
        if not self.coiled:
            return None

        # Channel breakout levels (prior N bars excluding current)
        prior_highs = highs[-self.channel_len - 1 : -1]
        prior_lows = lows[-self.channel_len - 1 : -1]
        breakout_high = max(prior_highs)
        breakout_low = min(prior_lows)

        close = ctx.bar.close
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Cross-asset trend / momentum confirmation to fix prior OOS failures
        # Long: breakout above prior channel high confirmed by non-negative BTC momentum
        if close > breakout_high and btc_ret > -1.0:
            self.coiled = False
            self.coil_bars_left = 0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "post_spike_coil_long_breakout",
                    "vol_ratio": round(vol_ratio, 3),
                    "breakout_high": breakout_high,
                    "btc_ret": round(btc_ret, 2),
                    "close": close,
                },
            )

        # Short: breakdown below prior channel low confirmed by non-positive BTC momentum
        if close < breakout_low and btc_ret < 1.0:
            self.coiled = False
            self.coil_bars_left = 0
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "post_spike_coil_short_breakout",
                    "vol_ratio": round(vol_ratio, 3),
                    "breakout_low": breakout_low,
                    "btc_ret": round(btc_ret, 2),
                    "close": close,
                },
            )

        return None