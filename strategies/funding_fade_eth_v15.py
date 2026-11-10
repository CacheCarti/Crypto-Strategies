from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "ETH Funding Contrarian Squeeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold horizon
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_long_thresh = -0.00015  # -0.015% funding: heavily short crowded
        self.funding_short_thresh = 0.00035  # +0.035% funding: heavily long crowded
        self.persist_bars = 3
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 8

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < 40:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Check existing position management: normalize exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Exit early when funding reverts to neutral to lock in mean-reversion
            if pos_dir == "long" and current_funding >= 0.00005:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_exit", "funding": current_funding, "close": ctx.bar.close}
                )
            if pos_dir == "short" and current_funding <= -0.00005:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_exit", "funding": current_funding, "close": ctx.bar.close}
                )
            return None

        # Mandatory Cooldown after exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Require funding persistence across recent bars
        if len(self.funding_history) < self.persist_bars:
            return None
        recent_funding = self.funding_history[-self.persist_bars:]

        funding_all_negative = all(f <= self.funding_long_thresh for f in recent_funding)
        funding_all_positive = all(f >= self.funding_short_thresh for f in recent_funding)

        if not funding_all_negative and not funding_all_positive:
            return None

        # Trend & volatility filters to avoid fading aggressive runaway trends
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        # Volatility check: avoid entering during extreme market-wide crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset momentum confirmation via BTC 24h return to confirm market stabilization
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        price = ctx.bar.close
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None

        # Entry logic:
        # Long squeeze setup: Crowd short (funding persistent negative), but price not in a free-fall (close >= EMA20 - 1.5 ATR)
        # and BTC return showing bottoming/stabilization (> -3.5%)
        if funding_all_negative:
            if price >= (ema20 - 1.5 * atr) and btc_ret > -3.5:
                avg_f = sum(recent_funding) / len(recent_funding)
                conf = min(0.85, 0.55 + abs(avg_f) * 1000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "short_squeeze_contrarian_entry",
                        "avg_funding": avg_f,
                        "btc_ret": btc_ret,
                        "ema20": ema20,
                        "price": price,
                    }
                )

        # Short squeeze setup: Crowd long (funding persistent positive), price not parabolic breakout (close <= EMA20 + 1.5 ATR)
        # and BTC return showing upside exhaustion (< 4.5%)
        if funding_all_positive:
            if price <= (ema20 + 1.5 * atr) and btc_ret < 4.5:
                avg_f = sum(recent_funding) / len(recent_funding)
                conf = min(0.85, 0.55 + abs(avg_f) * 1000.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "long_overcrowded_contrarian_entry",
                        "avg_funding": avg_f,
                        "btc_ret": btc_ret,
                        "ema20": ema20,
                        "price": price,
                    }
                )

        return None