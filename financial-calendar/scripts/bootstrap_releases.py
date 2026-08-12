"""One-time: discover FRED release_ids for the whitelist in config/events.yaml.

Why this exists: writing release_ids from memory is the highest-risk silent
error in this system — a wrong id means an A-tier event is permanently missing
and nothing ever raises an error. So ids are DISCOVERED from the live FRED
release list and every decision is written to an auditable report.

Outputs:
  config/release_ids.yaml    auto-accepted high-confidence matches (generated;
                             never hand-edit — edit events.yaml instead)
  config/events_review.yaml  ambiguous / not_found — needs a human
  logs/bootstrap_report.md   every decision, with the evidence behind it

Run once, then re-run only when the whitelist changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (CONFIG, LOGS, env_key, http_get, load_yaml, norm,  # noqa: E402
                    now_utc_iso, save_yaml)

FRED_RELEASES = "https://api.stlouisfed.org/fred/releases"


def fetch_releases(api_key: str) -> list[dict]:
    out, offset = [], 0
    while True:
        data = http_get(FRED_RELEASES, {
            "api_key": api_key, "file_type": "json",
            "limit": 1000, "offset": offset,
        })
        if not data:
            sys.stderr.write("[fatal] could not reach FRED releases endpoint.\n")
            raise SystemExit(3)
        batch = data.get("releases", [])
        out.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return out


def match(entry: dict, releases: list[dict]) -> tuple[str, list[dict], str]:
    """-> (confidence, candidates, evidence)

    high  : exactly one release whose normalized name equals a match string,
            or exactly one release containing a match string as a substring
    ambiguous : several candidates
    none  : nothing matched
    """
    patterns = [norm(p) for p in (entry.get("match") or [])]
    if not patterns:
        return "none", [], "白名单未提供 match 字符串"

    exact = [r for r in releases if norm(r["name"]) in patterns]
    if len(exact) == 1:
        return "high", exact, f"精确名称匹配：{exact[0]['name']}"
    if len(exact) > 1:
        return "ambiguous", exact, f"{len(exact)} 条精确匹配"

    subs = [r for r in releases
            if any(p in norm(r["name"]) for p in patterns)]
    if len(subs) == 1:
        return "high", subs, f"唯一子串匹配：{subs[0]['name']}"
    if len(subs) > 1:
        return "ambiguous", subs, f"{len(subs)} 条子串匹配"
    return "none", [], "无匹配"


def main() -> int:
    api_key = env_key("FRED_API_KEY")
    wl = load_yaml("events.yaml").get("macro") or []
    fred_entries = [e for e in wl if e.get("source") == "fred"]
    other = [e for e in wl if e.get("source") != "fred"]

    releases = fetch_releases(api_key)
    print(f"[info] fetched {len(releases)} FRED releases")

    resolved, review, rows = {}, [], []
    for e in fred_entries:
        conf, cands, evidence = match(e, releases)
        if conf == "high":
            r = cands[0]
            resolved[e["key"]] = {"release_id": int(r["id"]), "fred_name": r["name"]}
            rows.append((e["key"], e["label"], e.get("tier"), "AUTO-ACCEPT",
                         str(r["id"]), r["name"], evidence))
        else:
            review.append({
                "key": e["key"], "label": e["label"], "tier": e.get("tier"),
                "status": "ambiguous" if conf == "ambiguous" else "not_found",
                "match_strings": e.get("match") or [],
                "candidates": [{"release_id": int(c["id"]), "name": c["name"]}
                               for c in cands[:12]],
                "release_id": None,   # ← 人工填写后手动并入 release_ids.yaml
            })
            rows.append((e["key"], e["label"], e.get("tier"),
                         conf.upper(), "—", "—", evidence))

    save_yaml(CONFIG / "release_ids.yaml", resolved, header=(
        "# 自动生成 —— 请勿手工编辑。\n"
        "# 由 scripts/bootstrap_releases.py 从 FRED 发现；\n"
        "# 白名单与分级请改 config/events.yaml。\n"
        f"# 生成时间：{now_utc_iso()}"))

    save_yaml(CONFIG / "events_review.yaml", {"needs_review": review}, header=(
        "# bootstrap 无法高置信匹配的条目 —— 需人工处理。\n"
        "# 确认后把 key/release_id 手工写入 config/release_ids.yaml。\n"
        "# 不要凭记忆填写 release_id：请在 FRED 站内核对发布名称。"))

    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "bootstrap_report.md", "w", encoding="utf-8") as f:
        f.write(f"# bootstrap 决策报告\n\n生成时间：{now_utc_iso()}\n\n")
        f.write(f"FRED 发布总数：{len(releases)}　白名单 FRED 条目："
                f"{len(fred_entries)}　自动接受：{len(resolved)}　"
                f"待人工：{len(review)}　非 FRED 来源：{len(other)}\n\n")
        f.write("| key | 事件 | 分级 | 结果 | release_id | FRED 名称 | 依据 |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write("| " + " | ".join(str(x) for x in r) + " |\n")
        f.write("\n## 抽查方法\n\n"
                "任取 5 条 AUTO-ACCEPT，在 FRED 网站按 release_id 打开发布页，"
                "确认名称与下一次发布日期与本系统拉取结果一致。"
                "验收标准第 6 条即此项。\n")

    print(f"[ok] auto-accepted {len(resolved)}, needs review {len(review)}")
    print(f"[ok] report: {LOGS / 'bootstrap_report.md'}")
    if review:
        print("[action] 请处理 config/events_review.yaml 中的条目")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
