import pytest

from backend.services.silent_zones import (
    _build_grid,
    calculate_silence_score,
    polygon_for_area_id,
    tile_id,
)


def test_zero_report_cell_gets_maximum_silence_factor():
    score = calculate_silence_score(observed=0, expected=2.0)
    assert score["silence_factor"] == 1.0
    assert score["final_score"] == 1.0


def test_more_observed_reports_reduce_silence_score():
    no_reports = calculate_silence_score(observed=0, expected=2.0)
    some_reports = calculate_silence_score(observed=2, expected=2.0)
    assert some_reports["silence_factor"] < no_reports["silence_factor"]
    assert some_reports["final_score"] < no_reports["final_score"]


def test_grid_includes_zero_report_neighbour_cells():
    cells = _build_grid([(23.25, 77.40)])
    assert len(cells) == 9
    assert sum(cell["observed_reports"] for cell in cells) == 1
    assert sum(cell["observed_reports"] == 0 for cell in cells) == 8


def test_negative_grid_indices_round_trip_to_polygon():
    area_id = tile_id(-7740, -2325)
    polygon = polygon_for_area_id(area_id)
    assert polygon.is_valid
    assert polygon.area > 0


def test_invalid_expected_baseline_is_rejected():
    with pytest.raises(ValueError):
        calculate_silence_score(observed=1, expected=0)
