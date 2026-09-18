import json
from pathlib import Path

from scripts import update_game_data_from_p4k as importer


def test_version_label_prefers_matching_launcher_release(tmp_path: Path, monkeypatch) -> None:
    game_dir = tmp_path / "StarCitizen" / "LIVE"
    game_dir.mkdir(parents=True)
    (game_dir / "build_manifest.id").write_text(
        json.dumps({
            "Data": {
                "Branch": "sc-alpha-4.10.0",
                "RequestedP4ChangeNum": "12660092",
            }
        }),
        encoding="utf-8",
    )
    log_dir = tmp_path / "AppData" / "rsilauncher" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "log.log").write_text(
        "[Pipeline] Installing Star Citizen LIVE 4.10.1-live.12660092 at C:\\StarCitizen\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))

    assert importer.version_label(game_dir) == "SC-ALPHA-4.10.1-12660092"


def test_version_label_ignores_launcher_release_for_another_build(tmp_path: Path, monkeypatch) -> None:
    game_dir = tmp_path / "StarCitizen" / "LIVE"
    game_dir.mkdir(parents=True)
    (game_dir / "build_manifest.id").write_text(
        json.dumps({
            "Data": {
                "Branch": "sc-alpha-4.10.0",
                "RequestedP4ChangeNum": "12660092",
            }
        }),
        encoding="utf-8",
    )
    log_dir = tmp_path / "AppData" / "rsilauncher" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "log.log").write_text(
        "[Pipeline] Installing Star Citizen LIVE 4.11.0-live.99999999 at C:\\StarCitizen\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))

    assert importer.version_label(game_dir) == "SC-ALPHA-4.10.0-12660092"
