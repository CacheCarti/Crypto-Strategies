from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "SolBollingerSqueezeBreakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 125,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.squeeze_lookback = 100
        self.squeeze_threshold = 0.15
        self.min_cooldown_bars = 12
        self.cooldown = 0
        self.recent_squeeze_bars = 0

    def _bollinger(self, closes, period=20, num_std=2.0):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        upper = mean + num_std * std
        lower = mean - num_std * std
        bandwidth = (upper - lower) / mean if mean > 0 else 0.0
        return mean, upper, lower, bandwidth

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown = self.min_cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.squeeze_lookback + self.bb_period + 5)
        vols = ctx.volumes(24)

        if len(closes) < self.squeeze_lookback + self.bb_period:
            return None

        if self.cooldown > 0:
            self.cooldown -= 1

        # Avoid high crisis turbulence
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            return None

        current_bb = self._bollinger(closes, self.bb_period, self.bb_std)
        if current_bb is None:
            return None
        mid, upper, lower, current_bw = current_bb

        # Calculate historical bandwidth percentile over squeeze_lookback
        bw_history = []
        for i in range(len(closes) - self.squeeze_lookback, len(closes)):
            sub_bb = self._bollinger(closes[:i + 1], self.bb_period, self.bb_std)
            if sub_bb is not None:
                bw_history.append(sub_bb[3])

        if not bw_history:
            return None

        bw_percentile = sum(1 for x in bw_history if x <= current_bw) / len(bw_history)

        # Track squeeze state: strictly bottom 15th percentile
        if bw_percentile <= self.squeeze_threshold:
            self.recent_squeeze_bars = 2
        elif self.recent_squeeze_bars > 0:
            self.recent_squeeze_bars -= 1

        price = ctx.bar.close
        prev_close = closes[-2]
        rsi = self._rsi(closes, 14)
        avg_vol = sum(vols) / len(vols) if len(vols) > 0 else 1.0
        vol_surge = (ctx.bar.volume / avg_vol) >= 1.20

        # Position Management
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if price < mid:
                    self.cooldown = self.min_cooldown_bars
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "long_exit_midband_break",
                        "rsi": round(rsi, 2),
                        "price": round(price, 4),
                        "bb_mid": round(mid, 4)
                    })
            elif direction == "short":
                if price > mid:
                    self.cooldown = self.min_cooldown_bars
                    return ctx.signal("flat", confidence=0.75, metadata={
                        "reason": "short_exit_midband_break",
                        "rsi": round(rsi, 2),
                        "price": round(price, 4),
                        "bb_mid": round(mid, 4)
                    })
            return None

        # Entry Checks
        if self.cooldown > 0:
            return None

        # Require an active or very recent tight squeeze + clear breakout + volume surge
        if self.recent_squeeze_bars > 0 and vol_surge:
            # Bullish expansion
            if price > upper and prev_close <= upper and rsi > 56.0:
                self.cooldown = self.min_cooldown_bars
                return ctx.signal("long", confidence=0.8,
                                  stop_loss_bps=self.METADATA["declared_sl_bps"],
                                  take_profit_bps=self.METADATA["declared_tp_bps"],
                                  metadata={
                                      "reason": "bollinger_squeeze_breakout_long",
                                      "price": round(price, 4),
                                      "bb_upper": round(upper, 4),
                                      "bb_mid": round(mid, 4),
                                      "bw_percentile": round(bw_percentile, 3),
                                      "rsi": round(rsi, 2),
                                      "vol_ratio": round(ctx.bar.volume / avg_vol, 2)
                                  })

            # Bearish expansion
            if price < lower and prev_close >= lower and rsi < 44.0:
                self.cooldown = self.min_cooldown_bars
                return ctx.signal("short", confidence=0.8,
                                  stop_loss_bps=self.METADATA["declared_sl_bps"],
                                  take_profit_bps=self.METADATA["declared_tp_bps"],
                                  metadata={
                                      "reason": "bollinger_squeeze_breakout_short",
                                      "price": round(price, 4),
                                      "bb_lower": round(lower, 4),
                                      "bb_mid": round(mid, 4),
                                      "bw_percentile": round(bw_percentile, 3),
                                      "rsi": round(rsi, 2),
                                      "vol_ratio": round(ctx.bar.volume / avg_vol, 2)
                                  })

        return None