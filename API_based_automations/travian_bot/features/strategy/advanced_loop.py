import csv
import json
import os
import re
import time
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup

from analysis.number_to_unit_mapping import get_unit_name
from core.database_raid_config import load_saved_raid_plan, save_raid_plan
from features.building.building_planner import (
    _find_upgrade_url,
    _get_village_building_levels,
    _get_village_slot_catalog,
)
from features.hero.adventure_browser import run_adventure_browser_once
from features.hero.adventure_operations import run_adventure_once
from identity_handling.faction_utils import get_faction_name
from identity_handling.identity_helper import load_identity_data, load_villages_from_identity
from oasis_raiding_from_scan_list_main import run_raid_planner
from raid_list_main import run_one_farm_list_burst


STRATEGY_DIR = os.path.join("database", "strategy")
STRATEGY_CONFIG_FILE = os.path.join(STRATEGY_DIR, "advanced_strategy.json")
STRATEGY_CSV_FILE = os.path.join(STRATEGY_DIR, "advanced_strategy_plan.csv")
STRATEGY_XLSX_FILE = os.path.join(STRATEGY_DIR, "advanced_strategy_plan.xlsx")
STRATEGY_LOG_FILE = os.path.join(STRATEGY_DIR, "advanced_strategy_runtime.log")


DEFAULT_STRATEGY = {
    "version": 2,
    "max_build_queue": 2,
    "continuous_poll_seconds": 10,
    "run_farm_lists_each_cycle": True,
    "farm_lists_interval_minutes": 20,
    "run_oasis_raid_planner_each_cycle": True,
    "oasis_raid_planner_interval_minutes": 20,
    "run_hero_adventure_each_cycle": True,
    "hero_check_interval_seconds": 75,
    "hero_adventure_mode": "browser",  # browser | api
    "hero_browser_headless": True,
    "hero_watch_video_first": False,
    "auto_create_smart_oasis_raid_plans": True,
    "villages": {
        "*": {
            "phases": [
                {
                    "name": "phase_1_cranny_safety",
                    "notes": "Secure resources first before beginner protection ends.",
                    "rules": [
                        {
                            "kind": "building_name",
                            "contains_any": ["cranny", "cachette"],
                            "target_level": 10,
                        }
                    ],
                },
                {
                    "name": "phase_2_resource_push",
                    "notes": "Push economy and storage for stable growth.",
                    "rules": [
                        {"kind": "resource_fields", "target_level": 6},
                        {
                            "kind": "building_name",
                            "contains_any": ["main building", "batiment principal"],
                            "target_level": 10,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["warehouse", "entrepot"],
                            "target_level": 10,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["granary", "silo"],
                            "target_level": 10,
                        },
                    ],
                },
                {
                    "name": "phase_3_bootstrap_military",
                    "notes": "Get minimum military buildings online.",
                    "rules": [
                        {
                            "kind": "building_name",
                            "contains_any": ["rally point", "point de rassemblement"],
                            "target_level": 5,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["barracks", "caserne"],
                            "target_level": 10,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["stable", "ecurie"],
                            "target_level": 5,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["academy", "academie"],
                            "target_level": 5,
                        },
                    ],
                },
                {
                    "name": "phase_4_raid_defend_balance",
                    "notes": "Balance attack growth, defense and storage.",
                    "rules": [
                        {"kind": "resource_fields", "target_level": 10},
                        {
                            "kind": "building_name",
                            "contains_any": ["wall", "palisade", "earthen wall", "city wall", "stone wall"],
                            "target_level": 12,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["barracks", "caserne"],
                            "target_level": 15,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["stable", "ecurie"],
                            "target_level": 10,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["warehouse", "entrepot"],
                            "target_level": 16,
                        },
                        {
                            "kind": "building_name",
                            "contains_any": ["granary", "silo"],
                            "target_level": 16,
                        },
                    ],
                },
            ]
        }
    },
}


def _write_simple_xlsx(path: str, sheet_name: str, rows: list[list[str]]) -> None:
    def col_name(idx: int) -> str:
        name = ""
        n = idx
        while n > 0:
            n, rem = divmod(n - 1, 26)
            name = chr(65 + rem) + name
        return name

    def sheet_xml() -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
            "<sheetData>",
        ]
        for r_idx, row in enumerate(rows, start=1):
            lines.append(f'<row r="{r_idx}">')
            for c_idx, value in enumerate(row, start=1):
                cell_ref = f"{col_name(c_idx)}{r_idx}"
                safe = escape(str(value))
                lines.append(
                    f'<c r="{cell_ref}" t="inlineStr"><is><t>{safe}</t></is></c>'
                )
            lines.append("</row>")
        lines.extend(["</sheetData>", "</worksheet>"])
        return "".join(lines)

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf/></cellStyleXfs>
  <cellXfs count="1"><xf xfId="0"/></cellXfs>
</styleSheet>
"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml())


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _append_strategy_log(message: str) -> None:
    os.makedirs(STRATEGY_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    with open(STRATEGY_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def _load_or_create_strategy_config() -> dict:
    os.makedirs(STRATEGY_DIR, exist_ok=True)
    if not os.path.exists(STRATEGY_CONFIG_FILE):
        with open(STRATEGY_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_STRATEGY, f, indent=4, ensure_ascii=False)
        return DEFAULT_STRATEGY

    with open(STRATEGY_CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    changed = False
    for key, value in DEFAULT_STRATEGY.items():
        if key not in config:
            config[key] = value
            changed = True

    if changed:
        with open(STRATEGY_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    return config


def _serialize_plan_rows(config: dict) -> list[list[str]]:
    header = [
        "village_selector",
        "phase_order",
        "phase_name",
        "rule_order",
        "rule_kind",
        "contains_any_or_scope",
        "target_level",
        "notes",
    ]
    rows = [header]

    villages = config.get("villages", {})
    for village_selector, village_data in villages.items():
        phases = village_data.get("phases", [])
        for p_idx, phase in enumerate(phases, start=1):
            phase_name = phase.get("name", f"phase_{p_idx}")
            phase_notes = phase.get("notes", "")
            for r_idx, rule in enumerate(phase.get("rules", []), start=1):
                if rule.get("kind") == "resource_fields":
                    scope = "slots 1-18"
                else:
                    scope = ", ".join(rule.get("contains_any", []))
                rows.append(
                    [
                        str(village_selector),
                        str(p_idx),
                        phase_name,
                        str(r_idx),
                        rule.get("kind", ""),
                        scope,
                        str(rule.get("target_level", "")),
                        phase_notes,
                    ]
                )
    return rows


def ensure_strategy_files() -> tuple[str, str, str]:
    config = _load_or_create_strategy_config()
    rows = _serialize_plan_rows(config)

    with open(STRATEGY_CSV_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    _write_simple_xlsx(STRATEGY_XLSX_FILE, "StrategyPlan", rows)
    return STRATEGY_CONFIG_FILE, STRATEGY_CSV_FILE, STRATEGY_XLSX_FILE


def _get_village_strategy(config: dict, village_id: int) -> dict:
    villages = config.get("villages", {})
    return villages.get(str(village_id), villages.get("*", {}))


def _rule_candidates(rule: dict, levels: dict, catalog: list[dict]) -> list[dict]:
    target_level = int(rule.get("target_level", 0))
    if target_level <= 0:
        return []

    kind = rule.get("kind")
    if kind == "resource_fields":
        cands = []
        for slot_id in sorted(levels.keys()):
            if int(slot_id) > 18:
                continue
            current = int(levels.get(slot_id, 0))
            if current < target_level:
                cands.append({"slot_id": int(slot_id), "current": current, "target": target_level})
        cands.sort(key=lambda c: (c["current"], c["slot_id"]))
        return cands

    if kind == "building_name":
        patterns = [_normalize(x) for x in rule.get("contains_any", []) if str(x).strip()]
        if not patterns:
            return []
        cands = []
        for item in catalog:
            slot_id = int(item.get("slot_id"))
            current = int(item.get("level", 0))
            if current >= target_level:
                continue
            name = _normalize(item.get("name", ""))
            if any(p in name for p in patterns):
                cands.append({
                    "slot_id": slot_id,
                    "current": current,
                    "target": target_level,
                    "name": item.get("name", "unknown")
                })
        cands.sort(key=lambda c: (c["current"], c["slot_id"]))
        return cands

    return []


def _pick_next_target(strategy: dict, levels: dict, catalog: list[dict]) -> tuple[str, dict | None]:
    phases = strategy.get("phases", [])
    if not phases:
        return "no_phases", None

    for phase in phases:
        phase_name = phase.get("name", "unnamed_phase")
        for rule in phase.get("rules", []):
            candidates = _rule_candidates(rule, levels, catalog)
            if candidates:
                return phase_name, candidates[0]
    return phases[-1].get("name", "done"), None


def _pick_next_target_excluding(
    strategy: dict,
    levels: dict,
    catalog: list[dict],
    excluded_slots: set[int],
) -> tuple[str, dict | None]:
    phases = strategy.get("phases", [])
    if not phases:
        return "no_phases", None

    for phase in phases:
        phase_name = phase.get("name", "unnamed_phase")
        for rule in phase.get("rules", []):
            candidates = _rule_candidates(rule, levels, catalog)
            for candidate in candidates:
                if int(candidate["slot_id"]) not in excluded_slots:
                    return phase_name, candidate
    return phases[-1].get("name", "done"), None


def _parse_hms_to_seconds(text: str) -> int | None:
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text or "")
    if not m:
        return None
    if m.group(3) is None:
        return int(m.group(1)) * 60 + int(m.group(2))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def _get_build_queue_info(api, village_id: int) -> tuple[int, int | None]:
    url = f"{api.server_url}/dorf1.php?newdid={village_id}"
    response = api.session.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    block = soup.select_one(".buildingList")
    if not block:
        return 0, None

    queue_items = block.select("ul li")
    queue_count = len(queue_items)
    next_seconds = None
    for item in queue_items:
        timer_node = item.select_one(".timer")
        if not timer_node:
            continue
        seconds = _parse_hms_to_seconds(timer_node.get_text(" ", strip=True))
        if seconds is None:
            continue
        if next_seconds is None or seconds < next_seconds:
            next_seconds = seconds
    return queue_count, next_seconds


def _load_player_faction() -> str:
    identity = load_identity_data()
    tribe_id = identity["travian_identity"]["tribe_id"]
    fallback_faction = identity["travian_identity"].get("faction")
    return get_faction_name(tribe_id, fallback_faction=fallback_faction)


def _build_smart_distance_ranges(troops_info: dict, faction: str, max_raid_distance: int) -> list[dict]:
    excluded_keywords = (
        "hero", "scout", "pathfinder", "legati", "spotter",
        "ram", "catapult", "trebuchet", "ballista",
        "settler", "chief", "senator", "nomarch", "wagon",
    )
    candidates = []
    for code, count in troops_info.items():
        code = str(code)
        if not re.fullmatch(r"u\d{1,3}", code):
            continue
        if int(count) <= 0:
            continue
        unit_name = get_unit_name(code, faction).lower()
        if any(k in unit_name for k in excluded_keywords):
            continue
        candidates.append((code, int(count), unit_name))
    candidates.sort(key=lambda x: x[1], reverse=True)
    if not candidates:
        return []

    primary_code, primary_count, _ = candidates[0]
    secondary = candidates[1] if len(candidates) > 1 else None
    secondary_code = secondary[0] if secondary else None
    secondary_count = secondary[1] if secondary else 0

    g1 = max(1, min(6, primary_count // 40 if primary_count >= 40 else 1))
    g2 = max(g1 + 1, min(12, primary_count // 25 if primary_count >= 25 else g1 + 1))
    g3 = max(g2 + 1, min(20, primary_count // 16 if primary_count >= 16 else g2 + 1))
    s1 = 1 if secondary_count >= 25 else 0
    s2 = 2 if secondary_count >= 60 else (1 if secondary_count >= 30 else 0)

    def units_for(primary_n: int, secondary_n: int) -> list[dict]:
        units = [{
            "unit_code": primary_code,
            "unit_payload_code": primary_code,
            "group_size": max(1, min(primary_n, primary_count)),
        }]
        if secondary_code and secondary_n > 0:
            units.append({
                "unit_code": secondary_code,
                "unit_payload_code": secondary_code,
                "group_size": max(1, min(secondary_n, secondary_count)),
            })
        return units

    ranges = []
    if max_raid_distance <= 6:
        ranges.append({"start": 0, "end": max_raid_distance + 1, "units": units_for(g1, 0)})
        return ranges

    mid = min(6, max_raid_distance)
    far = min(10, max_raid_distance)
    if mid > 0:
        ranges.append({"start": 0, "end": mid, "units": units_for(g1, s1)})
    if far > mid:
        ranges.append({"start": mid, "end": far, "units": units_for(g2, s1)})
    if max_raid_distance > far:
        ranges.append({"start": far, "end": max_raid_distance + 1, "units": units_for(g3, s2)})
    return ranges


def _ensure_smart_oasis_raid_plans(api, server_url: str, force_rebuild: bool = False) -> None:
    villages = load_villages_from_identity()
    if not villages:
        return

    try:
        faction = _load_player_faction()
    except Exception as e:
        print(f"[smart-plan] Could not determine faction: {e}")
        return

    def plan_is_valid(plan: dict) -> bool:
        if not isinstance(plan, dict):
            return False
        for dr in plan.get("distance_ranges", []):
            for u in dr.get("units", []):
                code = str(u.get("unit_code", ""))
                if not re.fullmatch(r"u\d{1,3}", code):
                    return False
        return True

    for village_index, village in enumerate(villages):
        existing = load_saved_raid_plan(village_index)
        if existing and not force_rebuild and plan_is_valid(existing):
            continue
        if existing and not plan_is_valid(existing):
            print(f"[smart-plan] Rebuilding invalid existing plan for village index {village_index}.")

        village_id = int(village["village_id"])
        switch_url = f"{api.server_url}/dorf1.php?newdid={village_id}"
        response = api.session.get(switch_url)
        if response.status_code >= 400:
            print(f"[smart-plan] Skip village {village_id}: cannot switch (status {response.status_code}).")
            continue

        troops_info = api.get_troops_in_village()
        if not troops_info:
            print(f"[smart-plan] Skip village {village_id}: no troop info.")
            continue

        total_attack_troops = sum(
            int(v) for k, v in troops_info.items() if str(k).startswith("u") and k != "uhero"
        )
        if total_attack_troops < 5:
            print(f"[smart-plan] Skip village {village_id}: not enough troops ({total_attack_troops}).")
            continue

        max_raid_distance = 6 if total_attack_troops < 40 else (9 if total_attack_troops < 120 else 12)
        distance_ranges = _build_smart_distance_ranges(troops_info, faction, max_raid_distance)
        if not distance_ranges:
            print(f"[smart-plan] Skip village {village_id}: no suitable non-scout troop mix.")
            continue

        raid_plan = {
            "server": server_url,
            "village_index": village_index,
            "max_raid_distance": max_raid_distance,
            "distance_ranges": distance_ranges,
            "raid_plan": [],
        }
        save_raid_plan(raid_plan, server_url, village_index)
        print(f"[smart-plan] Created plan for village {village_id} (max distance {max_raid_distance}).")


def _run_hero_adventure_action(api, server_url: str, config: dict) -> bool:
    mode = str(config.get("hero_adventure_mode", "browser")).strip().lower()
    if mode == "browser":
        return run_adventure_browser_once(
            server_url=server_url,
            watch_video_first=bool(config.get("hero_watch_video_first", False)),
            headless=bool(config.get("hero_browser_headless", True)),
            session_cookies=api.session.cookies.get_dict(),
        )
    return run_adventure_once(api)


def run_advanced_strategy_cycle(api, server_url: str, config: dict | None = None, run_side_tasks: bool = True) -> dict:
    config = config or _load_or_create_strategy_config()
    villages = load_villages_from_identity()
    if not villages:
        print("No villages found in identity.")
        return {"started_upgrades": 0}

    print("\nAdvanced Strategy Cycle")
    started_upgrades = 0
    considered_villages = 0
    queue_full_villages = 0
    next_queue_seconds_candidates = []
    max_build_queue = int(config.get("max_build_queue", 2))
    cycle_plan_lines = []

    for village in villages:
        village_id = int(village["village_id"])
        village_name = village.get("village_name", f"village_{village_id}")
        considered_villages += 1

        print(f"\nVillage: {village_name} (ID: {village_id})")
        switch_url = f"{api.server_url}/dorf1.php?newdid={village_id}"
        switch_resp = api.session.get(switch_url)
        if switch_resp.status_code >= 400:
            print(f"  Failed to switch village (status {switch_resp.status_code}).")
            continue

        queue_count, next_seconds = _get_build_queue_info(api, village_id)
        print(f"  Build queue: {queue_count}/{max_build_queue}")
        cycle_plan_lines.append(
            f"Village {village_id} queue={queue_count}/{max_build_queue}"
        )
        if queue_count >= max_build_queue:
            queue_full_villages += 1
            if next_seconds is not None:
                next_queue_seconds_candidates.append(next_seconds)
            print("  Queue is full, waiting for a free slot.")
            cycle_plan_lines.append(
                f"Village {village_id} action=wait next_free_in={next_seconds}s"
            )
            continue

        strategy = _get_village_strategy(config, village_id)
        slots_to_fill = max(0, max_build_queue - queue_count)
        excluded_slots: set[int] = set()
        print(f"  Queue free slots: {slots_to_fill}")
        cycle_plan_lines.append(f"Village {village_id} plan_slots_to_fill={slots_to_fill}")

        for _ in range(slots_to_fill):
            levels = _get_village_building_levels(api, village_id)
            catalog = _get_village_slot_catalog(api, village_id)
            phase_name, target = _pick_next_target_excluding(strategy, levels, catalog, excluded_slots)
            print(f"  Active phase: {phase_name}")

            if not target:
                print("  No pending building target in configured phases.")
                cycle_plan_lines.append(f"Village {village_id} action=no_pending_target")
                break

            slot_id = int(target["slot_id"])
            current = int(target["current"])
            desired = int(target["target"])
            excluded_slots.add(slot_id)
            print(f"  slot {slot_id}: current {current}, target {desired} -> trying upgrade...")
            cycle_plan_lines.append(
                f"Village {village_id} target slot={slot_id} current={current} target={desired} phase={phase_name}"
            )
            upgrade_url = _find_upgrade_url(api, village_id, slot_id)
            if not upgrade_url:
                print("    No upgrade action found (queue full, missing resources, or blocked action).")
                cycle_plan_lines.append(
                    f"Village {village_id} result slot={slot_id} status=no_upgrade_action"
                )
                break

            response = api.session.get(upgrade_url, allow_redirects=True)
            if response.status_code >= 400:
                print(f"    Upgrade request failed with status {response.status_code}.")
                cycle_plan_lines.append(
                    f"Village {village_id} result slot={slot_id} status=http_{response.status_code}"
                )
                break

            started_upgrades += 1
            queue_count += 1
            print("    Upgrade request sent.")
            cycle_plan_lines.append(
                f"Village {village_id} result slot={slot_id} status=upgrade_sent queue_now={queue_count}/{max_build_queue}"
            )
            if queue_count >= max_build_queue:
                break

    if run_side_tasks and config.get("auto_create_smart_oasis_raid_plans", True):
        print("\nEnsuring smart oasis raid plans...")
        try:
            _ensure_smart_oasis_raid_plans(api, server_url, force_rebuild=False)
        except Exception as e:
            print(f"Smart oasis plan generation error: {e}")

    if run_side_tasks and config.get("run_farm_lists_each_cycle", True):
        print("\nRunning farm-list burst...")
        try:
            run_one_farm_list_burst(api)
        except Exception as e:
            print(f"Farm-list burst error: {e}")

    if run_side_tasks and config.get("run_oasis_raid_planner_each_cycle", True):
        print("\nRunning oasis raid planner...")
        try:
            run_raid_planner(
                api=api,
                server_url=server_url,
                reuse_saved=True,
                multi_village=True,
                run_farm_lists=False,
                interactive=False,
            )
        except Exception as e:
            print(f"Oasis raid planner error: {e}")

    if run_side_tasks and config.get("run_hero_adventure_each_cycle", True):
        print("\nHero check...")
        try:
            _run_hero_adventure_action(api, server_url, config)
        except Exception as e:
            print(f"Hero adventure error: {e}")

    print(
        f"\nAdvanced cycle done. Villages checked: {considered_villages}. "
        f"Upgrades started: {started_upgrades}."
    )
    _append_strategy_log("=== cycle start ===")
    for line in cycle_plan_lines:
        _append_strategy_log(line)
    _append_strategy_log(
        f"Cycle summary villages={considered_villages} upgrades_started={started_upgrades} "
        f"queue_full_villages={queue_full_villages} next_queue_seconds="
        f"{min(next_queue_seconds_candidates) if next_queue_seconds_candidates else None}"
    )
    _append_strategy_log("=== cycle end ===")
    return {
        "started_upgrades": started_upgrades,
        "considered_villages": considered_villages,
        "queue_full_villages": queue_full_villages,
        "next_queue_seconds": min(next_queue_seconds_candidates) if next_queue_seconds_candidates else None,
    }


def run_advanced_strategy_loop(api, server_url: str, max_cycles: int | None = None) -> None:
    config = _load_or_create_strategy_config()
    poll_seconds = 10.0
    farm_interval = max(1.0, float(config.get("farm_lists_interval_minutes", 20))) * 60
    oasis_interval = max(1.0, float(config.get("oasis_raid_planner_interval_minutes", 20))) * 60
    hero_interval = max(20.0, float(config.get("hero_check_interval_seconds", 75)))

    cycle_idx = 0
    last_farm_ts = 0.0
    last_oasis_ts = 0.0
    last_hero_ts = 0.0

    if config.get("auto_create_smart_oasis_raid_plans", True):
        try:
            _ensure_smart_oasis_raid_plans(api, server_url, force_rebuild=False)
        except Exception as e:
            print(f"Smart oasis plan generation error: {e}")

    while True:
        cycle_idx += 1
        print(f"\n{'=' * 56}")
        print(f"Continuous Cycle #{cycle_idx} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 56}")
        cycle_result = run_advanced_strategy_cycle(api, server_url, config=config, run_side_tasks=False)

        now_ts = time.time()
        if config.get("run_farm_lists_each_cycle", True) and now_ts - last_farm_ts >= farm_interval:
            print("\nInterval trigger: farm-list burst")
            try:
                run_one_farm_list_burst(api)
            except Exception as e:
                print(f"Farm-list burst error: {e}")
            last_farm_ts = time.time()

        if config.get("run_oasis_raid_planner_each_cycle", True) and now_ts - last_oasis_ts >= oasis_interval:
            print("\nInterval trigger: oasis raid planner")
            try:
                run_raid_planner(
                    api=api,
                    server_url=server_url,
                    reuse_saved=True,
                    multi_village=True,
                    run_farm_lists=False,
                    interactive=False,
                )
            except Exception as e:
                print(f"Oasis raid planner error: {e}")
            last_oasis_ts = time.time()

        if config.get("run_hero_adventure_each_cycle", True) and now_ts - last_hero_ts >= hero_interval:
            print("\nInterval trigger: hero check")
            try:
                _run_hero_adventure_action(api, server_url, config)
            except Exception as e:
                print(f"Hero adventure error: {e}")
            last_hero_ts = time.time()

        if max_cycles and cycle_idx >= max_cycles:
            print(f"\nReached max cycles ({max_cycles}). Stopping advanced loop.")
            return

        print(f"\nWaiting {poll_seconds:.1f}s before next queue check...")
        time.sleep(poll_seconds)
