from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load(name: str = "brands.yaml") -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text())


def own_brand(cfg: dict, brand_id: str) -> dict:
    for b in cfg["own"]:
        if b["id"] == brand_id:
            return b
    raise KeyError(f"unknown own brand '{brand_id}' — see config/brands.yaml")


def sync_brands(conn, cfg: dict):
    from . import db

    for b in cfg["own"]:
        db.upsert_brand(conn, id=b["id"], name=b["name"], kind="own", domain=b.get("domain"))
    for c in cfg.get("competitors") or []:
        db.upsert_brand(conn, id=c["id"], name=c["name"], kind="competitor",
                        domain=c.get("domain"), page_id=c.get("page_id"),
                        parent_brand=c.get("parent_brand"))
    conn.commit()
