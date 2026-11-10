from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class ConfluenceReversalEth(Strategy):
    METADATA = {
        "name": "ConfluenceReversalEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # 6 hours target hold on 1h bars
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.max_hold_bars = 18
        self.entry_bar_idx = 0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Position management
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar_idx
            
            # Normalization exits or time stop
            rsi_normalized = current_rsi >= 58.0
            funding_normalized = funding > 0.0001
            time_expired = bars_in_pos >= self.max_hold_bars
            crisis_exit = market_regime in ("CRISIS", "MELTDOWN")

            if rsi_normalized or funding_normalized or time_expired or crisis_exit:
                self.last_exit_bar = ctx.bar_index
                reason = "rsi_normalized" if rsi_normalized else (
                    "funding_positive" if funding_normalized else (
                        "max_hold_reached" if time_expired else "market_crisis"
                    )
                )
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "rsi": round(current_rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_in_pos
                    }
                )
            return None

        # Entry logic
        # Cooldown guard: wait required bars after last exit to prevent clustering
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries in catastrophic meltdowns
        if market_regime == "MELTDOWN":
            return None

        # Robust confluence criteria:
        # 1. Negative funding (crowd heavily short or paying shorts)
        # 2. Broader market fear (fear_greed < 40)
        # 3. Oversold momentum bounce setup (RSI < 38)
        cond_funding = funding <= 0.0
        cond_sentiment = fg_index < 40.0
        cond_oversold = current_rsi < 38.0

        if cond_funding and cond_sentiment and cond_oversold:
            # Score confidence based on depth of oversold & fear
            depth_bonus = 0.0
            if current_rsi < 30.0:
                depth_bonus += 0.15
            if fg_index < 25.0:
                depth_bonus += 0.15
            if funding < -0.00005:
                depth_bonus += 0.10

            confidence = min(0.60 + depth_bonus, 0.95)
            self.entry_bar_idx = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=550.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "triple_confluence_reversal",
                    "rsi": round(current_rsi, 2),
                    "funding": round(funding, 6),
                    "fear_greed": round(fg_index, 1),
                    "regime": market_regime
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index