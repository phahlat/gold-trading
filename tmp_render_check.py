from pathlib import Path
import pandas as pd
from bot.src.infrastructure.charting.live_plot import LiveChartRenderer

frame = pd.DataFrame(
    {
        "datetime": ["2026-07-30 00:00:00", "2026-07-30 00:15:00"],
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
    }
)
renderer = LiveChartRenderer(output_dir=Path("logs"), interactive=True, chart_width=10, chart_height=4, max_lower_candles=20, max_higher_candles=20)
path = renderer.render_dual_timeframe(
    lower_frame=frame,
    higher_frame=frame,
    symbol="XAUUSD",
    lower_timeframe="M15",
    higher_timeframe="H1",
    output_name="latest_render_check.png",
)
print(path)
print(path.exists())
