from pathlib import Path

from strategy.market_data import load_csv_candles
from trading_bot.market_data_seed import ensure_default_market_data


def test_default_market_data_is_valid_and_not_overwritten(tmp_path: Path) -> None:
    path = ensure_default_market_data(tmp_path)
    candles = load_csv_candles(path, minimum_history=20)

    assert path.name == "market_data.csv"
    assert len(candles) == 24

    path.write_text("custom", encoding="utf-8")
    assert ensure_default_market_data(tmp_path).read_text(encoding="utf-8") == "custom"
