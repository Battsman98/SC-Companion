"""Build matching Chromium/Firefox downloads from an explicit source allowlist."""
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "rsi-connector-extension"
FILES = ["background.js", "inventory.js", "content.js", "popup.html", "popup.css",
         "preview.html", "preview.js", "preview.css", "README.md"]


def build() -> None:
    version = json.loads((SOURCE / "manifest.json").read_text())["version"]
    assert (SOURCE / "inventory.js").read_bytes() == (ROOT / "web/rsi-inventory.js").read_bytes()
    for variant, manifest in [("", "manifest.json"), ("-firefox", "manifest.firefox.json")]:
        assert json.loads((SOURCE / manifest).read_text())["version"] == version
        destination = ROOT / "web" / f"rsi-connector-extension{variant}-v{version}.zip"
        with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
            archive.write(SOURCE / manifest, "manifest.json")
            for name in FILES:
                archive.write(SOURCE / name, name)
            for icon in sorted((SOURCE / "icons").glob("*.png")):
                archive.write(icon, f"icons/{icon.name}")
        with ZipFile(destination) as archive:
            assert archive.testzip() is None
            for name in FILES:
                assert archive.read(name) == (SOURCE / name).read_bytes()
        print(destination)


if __name__ == "__main__":
    build()
