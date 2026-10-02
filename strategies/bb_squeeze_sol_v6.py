from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "SOL Bollinger Squeeze Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 120,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.hist_window = 100
        self.squeeze_quantile = 0.15
        self.cooldown_bars = 16
        self.last_trade_bar = -100
        self.last_exit_bar = -100

    def _bollinger(self, closes, period, num_std):
        if len(closes) < period:
            return None, None, None, None
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
            return None
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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + self.hist_window + 5)
        volumes = ctx.volumes(25)
        if len(closes) < self.bb_period + self.hist_window or len(volumes) < 20:
            return None

        current_idx = ctx.bar_index
        mid, upper, lower, current_bw = self._bollinger(closes, self.bb_period, self.bb_std)
        if mid is None or upper is None or lower is None or current_bw is None:
            return None

        # Build historical bandwidth distribution to identify true low-volatility compression
        bw_history = []
        for offset in range(self.hist_window):
            sub_closes = closes[:len(closes) - offset]
            _, _, _, bw = self._bollinger(sub_closes, self.bb_period, self.bb_std)
            if bw is not None:
                bw_history.append(bw)

        if len(bw_history) < self.hist_window:
            return None

        sorted_bw = sorted(bw_history)
        threshold_idx = int(len(sorted_bw) * self.squeeze_quantile)
        squeeze_threshold = sorted_bw[threshold_idx]
        is_squeeze = current_bw <= squeeze_threshold

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        price = ctx.bar.close
        prev_close = closes[-2]
        prev_mid, prev_upper, prev_lower, _ = self._bollinger(closes[:-1], self.bb_period, self.bb_std)

        # Active position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit when momentum decisively breaks back below the 20-SMA midline or reaches extreme exhaustion
                if price < mid and prev_close < mid:
                    self.last_exit_bar = current_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "sol_long_exit_lost_mid_support",
                            "price": price,
                            "mid": mid,
                            "rsi": rsi,
                        }
                    )
                if rsi > 82.0:
                    self.last_exit_bar = current_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "sol_long_exit_extreme_rsi_target",
                            "price": price,
                            "rsi": rsi,
                        }
                    )
            elif pos_dir == "short":
                # Exit when momentum decisively breaks back above the 20-SMA midline or reaches extreme exhaustion
                if price > mid and prev_close > mid:
                    self.last_exit_bar = current_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "sol_short_exit_lost_mid_resistance",
                            "price": price,
                            "mid": mid,
                            "rsi": rsi,
                        }
                    )
                if rsi < 18.0:
                    self.last_exit_bar = current_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "sol_short_exit_extreme_rsi_target",
                            "price": price,
                            "rsi": rsi,
                        }
                    )
            return None

        # Hard multi-bar cooldown guard to restrict overtrading and avoid fee drag
        if (current_idx - self.last_trade_bar < self.cooldown_bars) or (current_idx - self.last_exit_bar < self.cooldown_bars):
            return None

        # Regime gating: reject trade entries during crisis or meltdown volatility
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volume expansion filter: breakout bar must exceed average volume
        avg_vol = sum(volumes[-20:]) / 20.0
        vol_expansion = ctx.bar.volume > (1.15 * avg_vol)

        # Check for confirmed squeeze in recent 2 bars
        was_recent_squeeze = False
        for offset in range(1, 3):
            sub_closes = closes[:len(closes) - offset]
            _, _, _, bw = self._bollinger(sub_closes, self.bb_period, self.bb_std)
            if bw is not None and bw <= squeeze_threshold:
                was_recent_squeeze = True
                break

        valid_squeeze_origin = is_squeeze or was_recent_squeeze

        # Long breakout: fresh break above upper Bollinger band with volume and RSI momentum
        if valid_squeeze_origin and vol_expansion and price > upper and (prev_upper is not None and prev_close <= prev_upper) and rsi > 56.0:
            self.last_trade_bar = current_idx
            confidence = min(0.9, 0.65 + (rsi - 50.0) / 100.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_bb_squeeze_upper_breakout_confirmed",
                    "price": price,
                    "upper_band": upper,
                    "mid_band": mid,
                    "bandwidth": current_bw,
                    "squeeze_threshold": squeeze_threshold,
                    "volume_ratio": round(ctx.bar.volume / avg_vol, 2) if avg_vol > 0 else 1.0,
                    "rsi": round(rsi, 2),
                }
            )

        # Short breakout: fresh break below lower Bollinger band with volume and RSI momentum
        if valid_squeeze_origin and vol_expansion and price < lower and (prev_lower is not None and prev_close >= prev_lower) and rsi < 44.0:
            self.last_trade_bar = current_idx
            confidence = min(0.9, 0.65 + (50.0 - rsi) / 100.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_bb_squeeze_lower_breakout_confirmed",
                    "price": price,
                    "lower_band": lower,
                    "mid_band": mid,
                    "bandwidth": current_bw,
                    "squeeze_threshold": squeeze_threshold,
                    "volume_ratio": round(ctx.bar.volume / avg_vol, 2) if avg_vol > 0 else 1.0,
                    "rsi": round(rsi, 2),
                }
            )

        return None