from app.core.catalog import (
    BY_ID, CATALOG, FREE_TWEAK_IDS, PRESETS, PRESET_META, PRESET_ORDER,
)
from app.core.catalog import managed_file, BLOCK_START, BLOCK_END


def test_managed_block_preserves_empty_lines_and_other_commands():
    old = {"value": f"{BLOCK_START}\r\n\r\ncl_showfps 1\r\nfps_max 0\r\n{BLOCK_END}\r\n"}
    result = managed_file(old, "fps_max 400")["value"]
    assert "\r\n\r\ncl_showfps 1" in result
    assert "fps_max 400" in result
    assert "fps_max 0" not in result


def test_presets_reference_known_unique_tweaks():
    for key, (_, ids) in PRESETS.items():
        assert len(ids) == len(set(ids)), key
        assert set(ids) <= set(BY_ID), key
    assert set(PRESETS) | {"custom"} == set(PRESET_META) == set(PRESET_ORDER)
    assert PRESET_ORDER == tuple(PRESET_META)


def test_free_preset_matches_the_shipped_free_set():
    assert tuple(PRESETS["free"][1]) == FREE_TWEAK_IDS
    assert len(FREE_TWEAK_IDS) == 3


def test_no_preset_contains_mutually_exclusive_tweaks():
    for key, (_, ids) in PRESETS.items():
        for tweak_id in ids:
            assert not (set(BY_ID[tweak_id].excludes) & set(ids)), key


def test_operations_are_unique_and_supported():
    seen = set()
    for tweak in CATALOG:
        for operation in tweak.operations:
            assert operation.id not in seen
            seen.add(operation.id)
            assert operation.kind in {"power", "file", "registry"}
