from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadReversion(Strategy):
    METADATA = {
        "name": "ETH-BTC Spread Mean Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.spread_long_threshold = -2.2
        self.spread_short_threshold = 2.4

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit management
        if has_pos:
            if pos_dir == "long":
                # Exit if spread has normalized or price crossed below slow EMA with high RSI
                if spread >= 0.8 or rsi >= 68.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_mean_reversion_target_hit",
                            "spread": spread,
                            "rsi": rsi,
                            "price": current_price,
                        },
                    )
            elif pos_dir == "short":
                # Exit if spread collapsed back to equilibrium or RSI oversold
                if spread <= -0.8 or rsi <= 32.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_mean_reversion_target_hit",
                            "spread": spread,
                            "rsi": rsi,
                            "price": current_price,
                        },
                    )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: ETH lagging BTC significantly, but not in terminal freefall
        if spread <= self.spread_long_threshold and rsi < 55.0 and btc_ret > -3.0:
            confidence = min(0.9, 0.55 + abs(spread) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_entry",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price,
                },
            )

        # Short Entry: ETH outrunning BTC by a wide margin, showing overextension
        if spread >= self.spread_short_threshold and rsi > 52.0:
            confidence = min(0.85, 0.55 + spread * 0.07)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_mean_reversion_entry",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price,
                },
            )

        return None