"""CLI. Run from ad-teardown/:  .venv/bin/python -m teardown <command>

  pull-own --brand nowa             own ads + creatives + insights from the Marketing API
  crawl-competitors --parent nowa   US Ad Library crawl for that brand's competitor set
  import <file.csv|file.json>       manual ads (validated; rejects written to a file)
  status                            what's in the database, per brand and lane
"""

from __future__ import annotations

import argparse
import sys

from . import config, db


def cmd_pull_own(args):
    cfg = config.load()
    brand = config.own_brand(cfg, args.brand)
    if not brand.get("enabled") and not args.force:
        sys.exit(f"brand '{args.brand}' is disabled in config/brands.yaml (use --force)")
    from .sources import meta_own

    conn = db.connect()
    config.sync_brands(conn, cfg)
    run_id = db.create_run(conn, {"brand_ids": [brand["id"]], "sources": ["meta_own"],
                                  "country": cfg["country"], "operator": "cli"})
    conn.commit()
    print(f"run {run_id} — pulling {brand['name']} from {cfg['ad_account_id']}")
    try:
        counters = meta_own.pull(conn, brand, cfg["ad_account_id"], run_id)
    except meta_own.GraphError as e:
        db.finish_run(conn, run_id, "failed", {"error": str(e)})
        conn.commit()
        sys.exit(f"failed: {e}")
    db.finish_run(conn, run_id, "partial" if counters["errors"] else "completed", counters)
    conn.commit()
    print({k: v for k, v in counters.items() if k != "errors"})
    for e in counters["errors"]:
        print("  error:", e)


def cmd_crawl_competitors(args):
    cfg = config.load()
    from .sources import meta_adlib

    conn = db.connect()
    config.sync_brands(conn, cfg)
    targets = [c for c in cfg.get("competitors") or []
               if c.get("parent_brand") == args.parent and c.get("page_id")
               and c.get("status") in ("proposed", "confirmed")
               and (not args.only or c["id"] in args.only.split(","))]
    run_id = db.create_run(conn, {"brand_ids": [c["id"] for c in targets],
                                  "sources": ["meta_adlib"], "country": cfg["country"],
                                  "operator": "cli"})
    conn.commit()
    counters, failed = {}, 0
    for c in targets:
        print(f"== {c['name']} ({c['page_id']}, {c['status']})", flush=True)
        try:
            result = meta_adlib.crawl(c["page_id"], cfg["country"], max_scroll=args.max_scroll)
        except Exception as e:  # noqa: BLE001 — one brand failing must not stop the others
            failed += 1
            counters[c["id"]] = {"error": str(e)[:200]}
            print("   failed:", str(e)[:200])
            continue
        counters[c["id"]] = meta_adlib.ingest(conn, c, result, run_id)
        counters[c["id"]]["reported"] = result["reported_results"]
        print(f"   reported {result['reported_results']} | {counters[c['id']]}", flush=True)
    status = "completed" if not failed else ("failed" if failed == len(targets) else "partial")
    db.finish_run(conn, run_id, status, counters)
    conn.commit()


def cmd_reparse_competitors(args):
    """Re-run the Ad Library parser over stored raw crawls (no network). Use after a
    parser fix; the latest raw file per brand wins."""
    import json

    from .sources import meta_adlib

    cfg = config.load()
    conn = db.connect()
    run_id = db.create_run(conn, {"sources": ["meta_adlib"], "mode": "reparse", "operator": "cli"})
    counters = {}
    for c in cfg.get("competitors") or []:
        files = sorted((db.DATA_DIR / "raw" / "meta_adlib" / c["id"]).glob("*.json"))
        if files:
            counters[c["id"]] = meta_adlib.ingest(conn, c, json.loads(files[-1].read_text()), run_id)
            print(c["id"], counters[c["id"]])
    db.finish_run(conn, run_id, "completed", counters)
    conn.commit()


def cmd_media(args):
    from . import media

    conn = db.connect()
    brands = args.brands.split(",") if args.brands else None
    print(media.process(conn, brands, args.lane, workers=args.workers,
                        retry_errors=args.retry_errors))


def cmd_landing(args):
    from . import landing

    conn = db.connect()
    brands = args.brands.split(",") if args.brands else None
    run_id = db.create_run(conn, {"stage": "landing", "brand_ids": brands, "operator": "cli"})
    conn.commit()
    counters = landing.run(conn, run_id, brands, force=args.force)
    db.finish_run(conn, run_id, "completed", counters)
    conn.commit()
    print(counters)


def cmd_evidence(args):
    from . import evidence

    conn = db.connect()
    brands = args.brands.split(",") if args.brands else None
    print(evidence.build(conn, brands, with_asr=not args.no_asr))


def cmd_import(args):
    from .sources import csv_import

    conn = db.connect()
    config.sync_brands(conn, config.load())
    run_id = db.create_run(conn, {"sources": ["csv"], "file": args.file, "operator": "cli"})
    counters = csv_import.import_file(conn, args.file, run_id)
    db.finish_run(conn, run_id, "partial" if counters["rejected"] else "completed", counters)
    conn.commit()
    print(counters)


def cmd_status(args):
    conn = db.connect()
    print("ads by brand / lane / status:")
    for r in conn.execute("""SELECT brand_id, lane, active_status, format, COUNT(*) n
                             FROM ads GROUP BY 1,2,3,4 ORDER BY 1,2,3,4"""):
        print(f"  {r['brand_id']:<10} {r['lane']:<11} {r['active_status']:<22} {r['format']:<13} {r['n']}")
    r = conn.execute("""SELECT COUNT(DISTINCT ad_id) ads, ROUND(SUM(spend),2) spend
                        FROM ad_metrics WHERE window='lifetime' AND country='US'""").fetchone()
    print(f"US lifetime metrics: {r['ads']} ads, ${r['spend']} spend")
    for r in conn.execute("SELECT id, status, created_at, counters FROM runs "
                          "ORDER BY created_at DESC LIMIT 3"):
        print(f"  run {r['id'][:8]} {r['status']:<10} {r['created_at']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="teardown")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull-own")
    p.add_argument("--brand", default="nowa")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_pull_own)
    p = sub.add_parser("crawl-competitors")
    p.add_argument("--parent", default="nowa")
    p.add_argument("--only", help="comma-separated competitor ids")
    p.add_argument("--max-scroll", type=int, default=40)
    p.set_defaults(fn=cmd_crawl_competitors)
    sub.add_parser("reparse-competitors").set_defaults(fn=cmd_reparse_competitors)
    p = sub.add_parser("media")
    p.add_argument("--brands")
    p.add_argument("--lane", choices=["own", "competitor"])
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--retry-errors", action="store_true")
    p.set_defaults(fn=cmd_media)
    p = sub.add_parser("landing")
    p.add_argument("--brands")
    p.add_argument("--force", action="store_true", help="ignore the 24 h page cache")
    p.set_defaults(fn=cmd_landing)
    p = sub.add_parser("evidence")
    p.add_argument("--brands")
    p.add_argument("--no-asr", action="store_true")
    p.set_defaults(fn=cmd_evidence)
    p = sub.add_parser("import")
    p.add_argument("file")
    p.set_defaults(fn=cmd_import)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
