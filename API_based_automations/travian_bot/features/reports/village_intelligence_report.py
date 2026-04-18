import json
import os
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from features.building.building_planner import (
    _get_village_level_state,
    _get_village_slot_catalog,
)
from features.strategy.advanced_loop import (
    _get_village_strategy,
    _load_or_create_strategy_config,
    _pick_next_target_excluding,
)
from identity_handling.identity_helper import load_villages_from_identity


BOT_ROOT_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT_DIR = Path(__file__).resolve().parents[4]


def _clean_int(text: str | None) -> int | None:
    if text is None:
        return None
    cleaned = re.sub(r"[^\d\-]", "", str(text))
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _resolve_report_dir() -> str:
    candidates = []
    for base in [Path.cwd(), BOT_ROOT_DIR, WORKSPACE_ROOT_DIR]:
        p = (base / "database" / "reports").resolve()
        if p not in candidates:
            candidates.append(p)
    for p in candidates:
        parent = p.parent
        if parent.exists():
            os.makedirs(p, exist_ok=True)
            return str(p)
    os.makedirs(candidates[0], exist_ok=True)
    return str(candidates[0])


def _load_villages_merged(api) -> list[dict]:
    identity_villages = []
    try:
        identity_villages = load_villages_from_identity()
    except Exception:
        identity_villages = []

    by_id = {}
    for v in identity_villages:
        try:
            vid = int(v.get("village_id"))
        except Exception:
            continue
        by_id[vid] = dict(v)

    ordered_ids = []
    try:
        info = api.get_player_info()
        api_villages = info.get("villages", []) if isinstance(info, dict) else []
        for v in api_villages:
            vid = int(v.get("id"))
            ordered_ids.append(vid)
            merged = by_id.get(vid, {})
            merged["village_id"] = vid
            name = str(v.get("name", "")).strip()
            if name:
                merged["village_name"] = name
            else:
                merged.setdefault("village_name", f"village_{vid}")
            merged["sort_index"] = v.get("sortIndex")
            merged["tribe_id"] = v.get("tribeId")
            by_id[vid] = merged
    except Exception:
        pass

    if not by_id:
        return []

    if not ordered_ids:
        ordered_ids = sorted(by_id.keys())
    else:
        seen = set(ordered_ids)
        for vid in sorted(by_id.keys()):
            if vid not in seen:
                ordered_ids.append(vid)

    out = []
    for vid in ordered_ids:
        row = dict(by_id[vid])
        row.setdefault("village_id", vid)
        row.setdefault("village_name", f"village_{vid}")
        out.append(row)
    return out


def _parse_resource_block(dorf1_html: str) -> dict:
    soup = BeautifulSoup(dorf1_html, "html.parser")
    resources = {
        "lumber": _clean_int((soup.find(id="l1") or {}).get_text(strip=True) if soup.find(id="l1") else None),
        "clay": _clean_int((soup.find(id="l2") or {}).get_text(strip=True) if soup.find(id="l2") else None),
        "iron": _clean_int((soup.find(id="l3") or {}).get_text(strip=True) if soup.find(id="l3") else None),
        "crop": _clean_int((soup.find(id="l4") or {}).get_text(strip=True) if soup.find(id="l4") else None),
    }

    stock = soup.find(id="stockBar")
    warehouse_cap = None
    granary_cap = None
    free_crop = _clean_int((soup.find(id="stockBarFreeCrop") or {}).get_text(strip=True) if soup.find(id="stockBarFreeCrop") else None)
    per_resource_meta = {}

    if stock:
        warehouse_cap = _clean_int((stock.select_one(".warehouse .value") or {}).get_text(strip=True) if stock.select_one(".warehouse .value") else None)
        granary_cap = _clean_int((stock.select_one(".granary .value") or {}).get_text(strip=True) if stock.select_one(".granary .value") else None)
        for idx, key in enumerate(("lumber", "clay", "iron", "crop"), start=1):
            btn = stock.select_one(f"a.resource{idx}")
            if not btn:
                continue
            title = str(btn.get("title", ""))
            prod_match = re.search(r"Production(?: less building upkeep)?\s*:\s*([0-9,]+)", title, flags=re.IGNORECASE)
            full_match = re.search(r"Full in:\s*([0-9:]+)", title, flags=re.IGNORECASE)
            per_resource_meta[key] = {
                "production_per_hour_from_title": _clean_int(prod_match.group(1)) if prod_match else None,
                "full_in": full_match.group(1) if full_match else None,
            }

    prod_table = soup.find(id="production")
    prod_map = {}
    if prod_table:
        rows = prod_table.select("tbody tr")
        for row in rows:
            icon = row.select_one("i")
            num_td = row.select_one("td.num")
            if not icon or not num_td:
                continue
            cls = " ".join(icon.get("class", []))
            val = _clean_int(num_td.get_text(" ", strip=True))
            if "r1" in cls:
                prod_map["lumber"] = val
            elif "r2" in cls:
                prod_map["clay"] = val
            elif "r3" in cls:
                prod_map["iron"] = val
            elif "r4" in cls:
                prod_map["crop"] = val

    return {
        "current_resources": resources,
        "production_per_hour": prod_map,
        "storage": {
            "warehouse_capacity": warehouse_cap,
            "granary_capacity": granary_cap,
            "free_crop": free_crop,
        },
        "resource_meta": per_resource_meta,
    }


def _parse_build_queue(dorf1_html: str) -> dict:
    soup = BeautifulSoup(dorf1_html, "html.parser")
    block = soup.select_one(".buildingList")
    if not block:
        return {"queue_count": 0, "items": []}

    items = []
    for li in block.select("ul li"):
        li_text = li.get_text(" ", strip=True)
        timer = li.select_one(".timer")
        timer_txt = timer.get_text(" ", strip=True) if timer else None
        slot_match = re.search(r"build\.php\?[^\"'<>]*\bid=(\d+)\b", str(li), flags=re.IGNORECASE)
        done_match = re.search(r"done at\s*([0-9:]+)", li_text, flags=re.IGNORECASE)
        items.append(
            {
                "slot_id": int(slot_match.group(1)) if slot_match else None,
                "timer": timer_txt,
                "done_at": done_match.group(1) if done_match else None,
                "text": li_text,
            }
        )
    return {"queue_count": len(items), "items": items}


def _top_troops(troops: dict, limit: int = 8) -> list[dict]:
    items = []
    for k, v in (troops or {}).items():
        try:
            count = int(v)
        except Exception:
            continue
        items.append({"unit": str(k), "count": count})
    items.sort(key=lambda x: x["count"], reverse=True)
    return items[:limit]


def _parse_troops_table(dorf1_html: str) -> dict:
    soup = BeautifulSoup(dorf1_html, "html.parser")
    table = soup.find(id="troops")
    if not table:
        return {"by_code": {}, "details": []}

    by_code = {}
    details = []
    for row in table.select("tbody tr"):
        img = row.find("img")
        num = row.find("td", class_="num")
        name_td = row.find("td", class_="un")
        if not img or not num:
            continue
        unit_code = None
        for c in img.get("class", []):
            if c == "uhero" or re.fullmatch(r"u\d{1,3}", str(c)):
                unit_code = c
                break
        if not unit_code:
            continue
        count = _clean_int(num.get_text(" ", strip=True))
        if count is None:
            continue
        unit_name = (name_td.get_text(" ", strip=True) if name_td else "") or img.get("alt", "")
        by_code[unit_code] = int(count)
        details.append(
            {
                "unit_code": unit_code,
                "unit_name": unit_name,
                "count": int(count),
            }
        )
    return {"by_code": by_code, "details": details}


def _collect_one_village(api, config: dict, village: dict) -> dict:
    village_id = int(village["village_id"])
    village_name = str(village.get("village_name", f"village_{village_id}"))
    x = village.get("x")
    y = village.get("y")

    switch_url = f"{api.server_url}/dorf1.php?newdid={village_id}"
    switch_resp = api.session.get(switch_url)
    switch_resp.raise_for_status()
    dorf1_html = switch_resp.text

    dorf2_resp = api.session.get(f"{api.server_url}/dorf2.php?newdid={village_id}")
    dorf2_resp.raise_for_status()

    eco = _parse_resource_block(dorf1_html)
    queue = _parse_build_queue(dorf1_html)
    troops_data = _parse_troops_table(dorf1_html)
    troops = troops_data.get("by_code", {})
    hero = api.get_hero_attributes()

    raw_levels, queued_counts, effective_levels = _get_village_level_state(api, village_id)
    catalog = _get_village_slot_catalog(api, village_id)
    for item in catalog:
        sid = int(item.get("slot_id", 0))
        item["raw_level"] = int(raw_levels.get(sid, item.get("level", 0)))
        item["queued"] = int(queued_counts.get(sid, 0))
        item["effective_level"] = int(effective_levels.get(sid, item.get("level", 0)))

    strategy = _get_village_strategy(config, village_id, village_name=village_name)
    blocked_slots = {int(sid) for sid, q in queued_counts.items() if int(q) > 0}
    phase_name, next_target = _pick_next_target_excluding(
        strategy,
        effective_levels,
        catalog,
        set(),
        blocked_slots=blocked_slots,
    )

    palace_rows = [
        r for r in catalog
        if any(k in str(r.get("name", "")).lower() for k in ("palace", "residence"))
    ]
    palace_summary = []
    for r in palace_rows:
        palace_summary.append(
            {
                "slot_id": int(r["slot_id"]),
                "name": r.get("name"),
                "level": int(r.get("effective_level", r.get("level", 0))),
                "ready_for_settlers_level_10": int(r.get("effective_level", r.get("level", 0))) >= 10,
            }
        )

    return {
        "village_id": village_id,
        "village_name": village_name,
        "coordinates": {"x": x, "y": y},
        "eco": eco,
        "build_queue": queue,
        "troops": troops,
        "troops_detailed": troops_data.get("details", []),
        "top_troops": _top_troops(troops),
        "hero_context": {
            "is_on_mission": bool(hero.get("isOnMission")) if isinstance(hero, dict) else None,
            "hero_health": hero.get("health") if isinstance(hero, dict) else None,
            "hero_current_village": hero.get("currentVillage") if isinstance(hero, dict) else None,
        },
        "buildings": catalog,
        "palace_or_residence": palace_summary,
        "strategy_next_candidate": {
            "phase": phase_name,
            "target": next_target,
            "blocked_slots": sorted(blocked_slots),
        },
    }


def _write_markdown_report(path: str, payload: dict) -> None:
    lines = []
    lines.append("# Village Intelligence Report")
    lines.append("")
    lines.append(f"- Generated at: {payload.get('generated_at')}")
    lines.append(f"- Server: {payload.get('server_url')}")
    lines.append(f"- Villages analyzed: {len(payload.get('villages', []))}")
    lines.append("")
    lines.append("## Strategic Focus")
    lines.append("- Goal captured: Prepare palace in village `1` for next-village expansion (Teuton raiding village).")
    lines.append("- Use this file + JSON in ChatGPT to pick final target coordinates and build order.")
    lines.append("")

    for v in payload.get("villages", []):
        eco = v.get("eco", {})
        cur = eco.get("current_resources", {})
        prod = eco.get("production_per_hour", {})
        store = eco.get("storage", {})
        lines.append(f"## Village {v.get('village_name')} (ID: {v.get('village_id')})")
        lines.append(f"- Coordinates: ({v.get('coordinates', {}).get('x')}, {v.get('coordinates', {}).get('y')})")
        lines.append(f"- Queue: {v.get('build_queue', {}).get('queue_count', 0)} item(s)")
        lines.append(f"- Resources now: wood={cur.get('lumber')} clay={cur.get('clay')} iron={cur.get('iron')} crop={cur.get('crop')}")
        lines.append(f"- Production/h: wood={prod.get('lumber')} clay={prod.get('clay')} iron={prod.get('iron')} crop={prod.get('crop')}")
        lines.append(f"- Storage: warehouse={store.get('warehouse_capacity')} granary={store.get('granary_capacity')} free_crop={store.get('free_crop')}")
        lines.append(f"- Next strategy target: {v.get('strategy_next_candidate', {}).get('target')}")
        palace = v.get("palace_or_residence", [])
        if palace:
            lines.append(f"- Palace/Residence status: {palace}")
        else:
            lines.append("- Palace/Residence status: not found in scanned slots")
        top = v.get("top_troops", [])
        lines.append(f"- Top troops: {top}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")


def _write_chatgpt_prompt(path: str, json_path: str, md_path: str) -> None:
    prompt = f"""Analyze my Travian villages from these files:
- JSON: {json_path}
- Markdown summary: {md_path}

My objective:
1) Upgrade palace (or residence if better) in village \"1\" to support founding a new village.
2) The new village should be optimized for Teuton raiding.
3) Provide the best coordinate criteria and exact short-term action plan from current state.

Please return:
- a strict prioritized checklist (next 10 actions),
- resource and queue-sensitive logic,
- where to delay military to avoid eco stall,
- and a concrete recommendation for best next-village placement profile for raiding.
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(prompt.strip() + "\n")


def run_village_intelligence_report(api, server_url: str) -> tuple[str, str, str]:
    config = _load_or_create_strategy_config()
    villages = _load_villages_merged(api)
    if not villages:
        raise RuntimeError("No villages available from API/identity.")

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "server_url": server_url,
        "strategy_file_version": config.get("version"),
        "villages": [],
    }

    for v in villages:
        report["villages"].append(_collect_one_village(api, config, v))

    report_dir = _resolve_report_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(report_dir, f"village_intelligence_{stamp}.json")
    md_path = os.path.join(report_dir, f"village_intelligence_{stamp}.md")
    prompt_path = os.path.join(report_dir, f"village_intelligence_{stamp}_chatgpt_prompt.txt")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    _write_markdown_report(md_path, report)
    _write_chatgpt_prompt(prompt_path, json_path, md_path)
    return json_path, md_path, prompt_path
