"""Tests for issue #16 — reserve-market ingest (mFRR EAM, aFRR, FCR-D)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from baseload.io import read_reserves, write_reserves
from ingest_reserves import load_reserve_zone, load_reserves_table

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_reserve_csv(path, start: str, periods: int, freq: str, directions=("Up", "Down")):
    idx = pd.date_range(start, periods=periods, freq=freq)
    step = idx[1] - idx[0]
    fmt = "%d/%m/%Y %H:%M:%S"
    rows = []
    rng = np.random.default_rng(1)
    for direction in directions:
        base = 60.0 if direction == "Up" else -15.0
        for i, ts in enumerate(idx):
            rows.append({
                "MTU": f"{ts.strftime(fmt)} - {(ts + step).strftime(fmt)}",
                "Direction": direction,
                "Price [EUR/MWh]": base + rng.normal(0, 5),
                "Volume [MW]": abs(rng.normal(50, 10)),
            })
    pd.DataFrame(rows).to_csv(path, index=False)
    return idx


class TestLoadReserveZone:
    def test_up_and_down_fields(self, tmp_path):
        csv = tmp_path / "no1_mfrr.csv"
        _write_reserve_csv(csv, "2025-10-01", 96, "15min")
        fields = load_reserve_zone(csv, freq="15min")
        assert set(fields) == {"up_price", "up_volume", "down_price", "down_volume"}
        assert fields["up_price"].notna().sum() == 96

    def test_missing_direction_column_maps_to_up(self, tmp_path):
        # Symmetric FCR-D capacity export: no Direction column.
        idx = pd.date_range("2025-10-01", periods=48, freq="h")
        fmt = "%d/%m/%Y %H:%M:%S"
        pd.DataFrame({
            "MTU": [f"{t.strftime(fmt)} - {(t + pd.Timedelta(hours=1)).strftime(fmt)}" for t in idx],
            "Price [EUR/MW/h]": 20.0,
            "Volume [MW]": 10.0,
        }).to_csv(tmp_path / "fcr.csv", index=False)
        fields = load_reserve_zone(tmp_path / "fcr.csv", freq="h")
        assert set(fields) == {"up_price", "up_volume"}

    def test_gaps_stay_gaps(self, tmp_path):
        # Reserve data only exists for activated MTUs — missing MTUs must stay NaN.
        csv = tmp_path / "no1_mfrr.csv"
        _write_reserve_csv(csv, "2025-10-01", 24, "h", directions=("Up",))
        table = load_reserves_table(
            {"NO1": "no1_mfrr.csv"}, tmp_path,
            start="2025-10-01T00:00:00Z", end="2025-10-02T23:00:00Z", freq="h",
        )
        # Second day has no data.
        assert table.loc["2025-10-02", ("NO1", "up_price")].isna().all()
        assert table.loc["2025-10-01", ("NO1", "up_price")].notna().all()


class TestEndToEnd:
    def test_one_zone_lands_with_schema_validation(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        _write_reserve_csv(raw / "no1_mfrr_eam.csv", "2025-10-01", 96 * 2, "15min")

        table = load_reserves_table(
            {"NO1": "no1_mfrr_eam.csv"}, raw,
            start="2025-10-01T00:00:00Z", end="2025-10-02T23:45:00Z", freq="15min",
        )
        assert list(table.columns.names) == ["_zone", "_field"]

        paths = {"processed": tmp_path / "processed"}
        paths["processed"].mkdir()
        write_reserves(table, paths, "mfrr_eam")   # schema-validates before writing
        back = read_reserves(paths, "mfrr_eam")     # schema-validates on load
        assert ("NO1", "up_price") in back.columns

    def test_multi_zone(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        _write_reserve_csv(raw / "no1.csv", "2025-10-01", 96, "15min")
        _write_reserve_csv(raw / "se3.csv", "2025-10-01", 96, "15min")
        table = load_reserves_table(
            {"NO1": "no1.csv", "SE3": "se3.csv"}, raw,
            start="2025-10-01T00:00:00Z", end="2025-10-01T23:45:00Z", freq="15min",
        )
        assert {"NO1", "SE3"} <= set(table.columns.get_level_values(0))

    @pytest.mark.skipif(
        not (REPO_ROOT / "no1_only_up.csv").exists(),
        reason="sample mFRR export not present",
    )
    def test_real_sample_export(self, tmp_path):
        # The NO1 up-regulation sample export in the repo root (hourly MTUs).
        fields = load_reserve_zone(REPO_ROOT / "no1_only_up.csv", freq="h")
        assert "up_price" in fields and "up_volume" in fields
        assert "down_price" not in fields
        assert fields["up_price"].notna().sum() > 100
