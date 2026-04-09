#!/usr/bin/env python3
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "scripts" / "pwa_assets.json"
MANIFEST_PATH = ROOT / "static" / "manifest.webmanifest"
INDEX_PATH = ROOT / "templates" / "index.html"
DASHBOARD_PATH = ROOT / "templates" / "dashboard.html"
SW_PATH = ROOT / "static" / "service-worker.js"
ICONS_DIR = ROOT / "static" / "icons"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"No existe config: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    required_top = ["cache_version", "icons"]
    for key in required_top:
        if key not in cfg:
            raise ValueError(f"Falta clave en config: {key}")
    required_icons = ["apple_touch", "icon_192", "icon_512", "icon_512_maskable"]
    for key in required_icons:
        if key not in cfg["icons"] or not str(cfg["icons"][key]).strip():
            raise ValueError(f"Falta icons.{key} en config")
    return cfg


def validate_icon_files(cfg: dict) -> None:
    missing = []
    for name in cfg["icons"].values():
        p = ICONS_DIR / name
        if not p.exists():
            missing.append(str(p))
    if missing:
        print("Aviso: faltan iconos (puedes continuar, pero revisa antes de deploy):")
        for m in missing:
            print(f" - {m}")


def update_manifest(cfg: dict) -> None:
    manifest = {
        "name": "AutoIndex",
        "short_name": "AutoIndex",
        "description": "Busqueda rapida en catalogos NAGS",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#020617",
        "theme_color": "#020617",
        "lang": "es-MX",
        "icons": [
            {
                "src": f"/static/icons/{cfg['icons']['icon_192']}",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": f"/static/icons/{cfg['icons']['icon_512']}",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": f"/static/icons/{cfg['icons']['icon_512_maskable']}",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": f"/static/icons/{cfg['icons']['apple_touch']}",
                "sizes": "180x180",
                "type": "image/png",
                "purpose": "any",
            },
        ],
    }
    with MANIFEST_PATH.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=True, indent=2)
        f.write("\n")


def update_apple_touch_icon(path: Path, cfg: dict) -> None:
    html = path.read_text(encoding="utf-8")
    new_tag = (
        f'<link rel="apple-touch-icon" sizes="180x180" '
        f'href="/static/icons/{cfg["icons"]["apple_touch"]}">'
    )
    pattern = r'<link rel="apple-touch-icon"[^>]*>'
    if re.search(pattern, html):
        html = re.sub(pattern, new_tag, html, count=1)
    else:
        head_close = "</head>"
        insert = f"    {new_tag}\n"
        idx = html.find(head_close)
        if idx == -1:
            raise ValueError(f"No se encontro </head> en {path.name}")
        html = html[:idx] + insert + html[idx:]
    path.write_text(html, encoding="utf-8", newline="\n")


def update_service_worker(cfg: dict) -> None:
    sw = SW_PATH.read_text(encoding="utf-8")
    new_cache = f'const CACHE_NAME = "autoindex-{cfg["cache_version"]}";'
    pattern = r'const CACHE_NAME = "[^"]+";'
    if not re.search(pattern, sw):
        raise ValueError("No se encontro CACHE_NAME en service-worker.js")
    sw = re.sub(pattern, new_cache, sw, count=1)
    SW_PATH.write_text(sw, encoding="utf-8", newline="\n")


def main() -> None:
    cfg = load_config()
    validate_icon_files(cfg)
    update_manifest(cfg)
    update_apple_touch_icon(INDEX_PATH, cfg)
    update_apple_touch_icon(DASHBOARD_PATH, cfg)
    update_service_worker(cfg)
    print("Listo: manifest, index.html, dashboard.html y service-worker actualizados.")


if __name__ == "__main__":
    main()
