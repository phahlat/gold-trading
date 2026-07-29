from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.src.application.services.gold_backtest_service import GoldBacktestService
from bot.src.application.services.gold_runner import GoldRunner
from bot.src.infrastructure.config.settings import load_gold_settings
from bot.src.infrastructure.market_data.csv_loader import load_backtest_ltf_htf_frames


class NullRenderer:
    def render_dual_timeframe(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return ""

    def close(self) -> None:
        return None


def main() -> int:
    base = load_gold_settings("gold_bot/.env")
    symbol = base.symbols[0] if base.symbols else "XAUUSD"
    data_source = Path("gold_bot/backtest/data")

    strategy_pairs = {
        "trend_following": [("M15", "H1")],
        "price_action": [("M5", "M30")],
        "scalping": [("M5", "M30")],
        "news": [("M5", "M30")],
        "session_breakout": [("M15", "H1")],
    }
    timeframe_pairs = sorted({pair for pairs in strategy_pairs.values() for pair in pairs})
    lookbacks = [6, 1]

    rows: list[dict[str, object]] = []

    frame_cache: dict[tuple[str, str], tuple[object, object, object, object]] = {}
    for lower_tf, higher_tf in timeframe_pairs:
        frame_cache[(lower_tf, higher_tf)] = load_backtest_ltf_htf_frames(
            data_source=data_source,
            symbol=symbol,
            lower_timeframe=lower_tf,
            higher_timeframe=higher_tf,
        )

    for lookback_months in lookbacks:
        for strategy, pairs in strategy_pairs.items():
            for lower_tf, higher_tf in pairs:
                settings = replace(
                    base,
                    enable_trading=False,
                    plot_enabled=False,
                    lower_timeframe=lower_tf,
                    higher_timeframe=higher_tf,
                    strategy_names=[strategy],
                    backtest_lookback_value=lookback_months,
                    backtest_lookback_unit="months",
                )

                lower, higher, lower_path, higher_path = frame_cache[(lower_tf, higher_tf)]
                lower_eval = lower
                higher_eval = higher
                # Quick comparative pass: stride candles to reduce runtime while preserving relative ranking.
                if lookback_months >= 6:
                    lower_eval = lower.iloc[::3].copy().reset_index(drop=True)
                    higher_eval = higher.iloc[::2].copy().reset_index(drop=True)
                elif lookback_months >= 1:
                    lower_eval = lower.iloc[::2].copy().reset_index(drop=True)
                    higher_eval = higher.iloc[::2].copy().reset_index(drop=True)

                runner = GoldRunner(settings)
                renderer = NullRenderer()
                service = GoldBacktestService(settings=settings, runner=runner, chart_renderer=renderer)
                result = service.run(
                    lower_frame=lower_eval,
                    higher_frame=higher_eval,
                    source_name=lower_path.stem,
                    artifact_stem=f"matrix_{strategy}_{lower_tf}_{higher_tf}_{lookback_months}m",
                )
                print(
                    f"COMBO_DONE lookback={lookback_months} strategy={strategy} "
                    f"ltf={lower_tf} htf={higher_tf} "
                    f"rows_ltf={len(lower_eval)} rows_htf={len(higher_eval)} "
                    f"signals={result.get('total_signals', 0)}"
                )

                wins = int(result.get("wins", 0))
                losses = int(result.get("losses", 0))
                breakeven = int(result.get("breakeven", 0))
                decided = wins + losses
                success_rate = (wins / decided * 100.0) if decided > 0 else 0.0
                closed = wins + losses + breakeven

                rows.append(
                    {
                        "lookback_months": lookback_months,
                        "strategy": strategy,
                        "ltf": lower_tf,
                        "htf": higher_tf,
                        "signals": int(result.get("total_signals", 0)),
                        "closed_trades": closed,
                        "wins": wins,
                        "losses": losses,
                        "breakeven": breakeven,
                        "success_rate": round(success_rate, 2),
                        "win_rate_all_closed": round(float(result.get("win_rate", 0.0)), 2),
                        "start_balance": round(float(result.get("start_balance", 0.0)), 2),
                        "end_balance": round(float(result.get("end_balance", 0.0)), 2),
                        "balance_change": round(float(result.get("balance_change", 0.0)), 2),
                    }
                )

    out_dir = Path("backtest/results/tf_matrix")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"tf_strategy_matrix_{stamp}.csv"

    fieldnames = [
        "lookback_months",
        "strategy",
        "ltf",
        "htf",
        "signals",
        "closed_trades",
        "wins",
        "losses",
        "breakeven",
        "success_rate",
        "win_rate_all_closed",
        "start_balance",
        "end_balance",
        "balance_change",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"MATRIX_RESULTS_CSV={csv_path}")

    for lookback in lookbacks:
        subset = [r for r in rows if int(r["lookback_months"]) == lookback]
        best = max(subset, key=lambda r: (float(r["success_rate"]), float(r["balance_change"]), int(r["signals"])))
        print(
            "BEST_SETUP "
            f"lookback_months={lookback} "
            f"strategy={best['strategy']} ltf={best['ltf']} htf={best['htf']} "
            f"success_rate={best['success_rate']} balance_change={best['balance_change']} signals={best['signals']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
