from app.core.steam import discover, library_paths, unescape


def test_library_paths_reads_every_library_and_unescapes():
    document = r'''"libraryfolders"
{
    "0" { "path" "C:\\Program Files (x86)\\Steam" }
    "1" { "path" "D:\\Games\\Steam" }
    "2" { "path" "D:\\Games\\Steam" }
}'''
    assert library_paths(document) == [
        r"C:\Program Files (x86)\Steam", r"D:\Games\Steam"]
    assert unescape(r"D:\\Games\\Steam") == r"D:\Games\Steam"
    assert library_paths("") == []


def test_discover_finds_cs2_in_a_secondary_library(tmp_path):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    library = tmp_path / "Library"
    game = library / "steamapps" / "common" / "Counter-Strike Global Offensive"
    config = game / "game" / "csgo" / "cfg"
    executable = game / "game" / "bin" / "win64" / "cs2.exe"
    config.mkdir(parents=True)
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    escaped = str(library).replace("\\", "\\\\")
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n    "1" { "path" "' + escaped + '" }\n}', encoding="utf-8")

    result = discover([str(steam)])

    assert result["autoexec"] == str(config / "autoexec.cfg")
    assert result["executable"] == str(executable)
    # Discovery is read-only: a missing autoexec.cfg is not created.
    assert not (config / "autoexec.cfg").exists()


def test_discover_without_steam_reports_nothing(tmp_path):
    result = discover([str(tmp_path / "missing")])
    assert result == {"steam": str(tmp_path / "missing"), "library": None,
                      "autoexec": None, "executable": None}
