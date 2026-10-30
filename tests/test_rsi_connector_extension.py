from pathlib import Path


EXTENSION_DIR = Path(__file__).resolve().parents[1] / "tools" / "rsi-connector-extension"


def test_rsi_connector_reports_paginated_scan_progress() -> None:
    background = (EXTENSION_DIR / "background.js").read_text(encoding="utf-8")
    content = (EXTENSION_DIR / "content.js").read_text(encoding="utf-8")
    manifest = (EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8")

    assert '"version": "0.6.0"' in manifest
    assert 'parser_version: "structured-inventory-v1"' in background
    assert "markup_fingerprint: fnv1a(structure)" in background
    assert "const controller = new AbortController()" in background
    assert "setTimeout(() => controller.abort(), 20000)" in background
    assert 'phase:"scanning"' in background
    assert 'direction: "from-game-assist-rsi-progress"' in background
    assert 'message.requestId = event.data.requestId' in content
    assert 'message?.direction !== "from-game-assist-rsi-progress"' in content


def test_rsi_connector_has_firefox_manifest_and_compatible_messaging() -> None:
    background = (EXTENSION_DIR / "background.js").read_text(encoding="utf-8")
    manifest = (EXTENSION_DIR / "manifest.firefox.json").read_text(encoding="utf-8")

    import json
    assert json.loads(manifest)["background"]["scripts"] == ["inventory.js", "background.js"]
    assert '"browser_specific_settings"' in manifest
    assert '"id": "rsi-hangar-importer@sccompanion.org"' in manifest
    assert '"strict_min_version": "109.0"' in manifest
    assert json.loads(manifest)["browser_specific_settings"]["gecko"]["data_collection_permissions"]["required"] == ["none"]
    assert "service_worker" not in manifest
    assert "() => void chrome.runtime.lastError" in background
    assert ".sendMessage(sender.tab.id" in background
    assert ").catch(() => {})" not in background


def test_inventory_behavior_suite() -> None:
    import shutil
    import subprocess
    node = shutil.which("node")
    assert node, "Node.js is required for the importer behavioral tests"
    subprocess.run([node, "--test", "tests/rsi_inventory.test.cjs"], cwd=EXTENSION_DIR.parents[1], check=True)
