from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadReversion(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Spread Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_window = 36
        self.rsi_period = 14
        self.cooldown_bars = 16
        self.max_hold_bars = 20
        self.z_entry_threshold = 2.20
        self.z_exit_threshold = 0.35
        
        self.spread_history = []
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(55)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Filter extreme crisis regimes
        if ctx.regime == "crisis" or ctx.market.get("regime") in ("CRISIS", "MELTDOWN"):
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > 160:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.spread_window:
            return None

        window_spreads = self.spread_history[-self.spread_window:]
        mean_spread = sum(window_spreads) / self.spread_window
        variance = sum((x - mean_spread) ** 2 for x in window_spreads) / self.spread_window
        std_spread = math.sqrt(variance)

        if std_spread < 1e-6:
            z_spread = 0.0
        else:
            z_spread = (spread - mean_spread) / std_spread

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position Management & Exit Rules
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if pos_dir == "long":
                if z_spread >= self.z_exit_threshold or rsi > 70.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_spread_normalized_or_time_exit",
                            "z_spread": round(z_spread, 3),
                            "spread": round(spread, 3),
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            elif pos_dir == "short":
                if z_spread <= -self.z_exit_threshold or rsi < 30.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_spread_normalized_or_time_exit",
                            "z_spread": round(z_spread, 3),
                            "spread": round(spread, 3),
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Mandatory Post-Exit Cooldown Gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # High conviction Long: ETH severely lagging BTC (Z <= -2.2) and local RSI confirming pullback (RSI <= 42)
        if z_spread <= -self.z_entry_threshold and rsi <= 42.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.65 + 0.08 * (abs(z_spread) - self.z_entry_threshold))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_lagging_btc_catchup_long",
                    "z_spread": round(z_spread, 3),
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                },
            )

        # High conviction Short: ETH severely outrunning BTC (Z >= 2.2) and local RSI confirming overextension (RSI >= 58)
        if z_spread >= self.z_entry_threshold and rsi >= 58.0:
            self.entry_bar = ctx.bar_index
            confidence = min(0.85, 0.65 + 0.08 * (z_spread - self.z_entry_threshold))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_outrunning_btc_mean_reversion_short",
                    "z_spread": round(z_spread, 3),
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                },
            )

        return None