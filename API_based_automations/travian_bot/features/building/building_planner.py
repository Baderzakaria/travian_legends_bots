import json
import os
import re
import html as html_lib
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from identity_handling.identity_helper import load_villages_from_identity


BUILDING_PLAN_DIR = os.path.join("database", "building_plans")
BUILDING_DEBUG_DIR = os.path.join("database", "building_debug")


def _get_plan_file(village_id: int) -> str:
    os.makedirs(BUILDING_PLAN_DIR, exist_ok=True)
    return os.path.join(BUILDING_PLAN_DIR, f"village_{village_id}.json")


def _extract_buildings_from_page(html: str) -> dict:
    """Extract slot levels from dorf page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    for slot in soup.select("[id^='aid']"):
        slot_id = slot.get("id", "")
        match = re.match(r"aid(\d+)", slot_id)
        if not match:
            continue
        aid = int(match.group(1))

        level = None
        class_text = " ".join(slot.get("class", []))
        level_match = re.search(r"\blevel(\d+)\b", class_text)
        if level_match:
            level = int(level_match.group(1))
        else:
            level_node = slot.select_one(".level")
            if level_node:
                text_match = re.search(r"(\d+)", level_node.get_text(" ", strip=True))
                if text_match:
                    level = int(text_match.group(1))

        if level is None:
            continue
        result[aid] = level

    # Fallback 1: parse explicit links like build.php?id=XX
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        match = re.search(r"build\.php\?id=(\d+)", href)
        if not match:
            continue
        aid = int(match.group(1))
        if aid not in result:
            result[aid] = 0

    # Fallback 1b: parse aidXX + level from class/title attributes.
    # Many worlds encode level in title: '...<span class="level">Level 7</span>...'
    for anchor in soup.find_all("a", href=True):
        class_text = " ".join(anchor.get("class", []))
        aid_match = re.search(r"\baid(\d+)\b", class_text)
        if not aid_match:
            # Newer worlds often expose dorf1 fields as buildingSlotXX levelYY
            aid_match = re.search(r"\bbuildingSlot(\d+)\b", class_text)
        if not aid_match:
            continue
        aid = int(aid_match.group(1))
        if aid not in result:
            result[aid] = 0

        class_level_match = re.search(r"\blevel(\d+)\b", class_text)
        if class_level_match:
            result[aid] = int(class_level_match.group(1))

        title_raw = anchor.get("title", "")
        if not title_raw:
            continue
        title_text = html_lib.unescape(title_raw)
        level_match = re.search(
            r"\b(?:level|niveau|stufe|nivel|livello)\s*(\d+)\b",
            title_text,
            flags=re.IGNORECASE,
        )
        if level_match:
            result[aid] = int(level_match.group(1))

    # Fallback 2: regex scan over raw HTML for aid and class-based levels.
    for aid_str, level_str in re.findall(r"aid(\d+)[^>]*\blevel(\d+)\b", html):
        aid = int(aid_str)
        level = int(level_str)
        if aid not in result or result[aid] == 0:
            result[aid] = level

    # Fallback 3: regex scan for slot IDs only.
    for aid_str in re.findall(r"build\.php\?id=(\d+)", html):
        aid = int(aid_str)
        if aid not in result:
            result[aid] = 0

    return result


def _get_village_building_levels(api, village_id: int) -> dict:
    """Read both dorf1 and dorf2 and merge levels."""
    levels = {}
    for page in ("dorf1.php", "dorf2.php"):
        url = f"{api.server_url}/{page}?newdid={village_id}"
        response = api.session.get(url)
        response.raise_for_status()
        levels.update(_extract_buildings_from_page(response.text))
    return levels


def _extract_queued_slot_ids_from_page(html: str) -> list[int]:
    """Extract queued slot IDs from the construction list block."""
    soup = BeautifulSoup(html, "html.parser")
    slot_ids: list[int] = []
    seen: set[int] = set()

    for item in soup.select(".buildingList ul li"):
        item_html = str(item)
        matches = re.findall(r"build\.php\?[^\"'<>]*\bid=(\d+)\b", item_html, flags=re.IGNORECASE)
        for match in matches:
            slot_id = int(match)
            if slot_id in seen:
                continue
            seen.add(slot_id)
            slot_ids.append(slot_id)
    return slot_ids


def _get_village_queued_upgrades(api, village_id: int) -> dict[int, int]:
    """Return queued upgrade counts by slot ID for this village."""
    url = f"{api.server_url}/dorf1.php?newdid={village_id}"
    response = api.session.get(url)
    response.raise_for_status()

    counts: dict[int, int] = {}
    for slot_id in _extract_queued_slot_ids_from_page(response.text):
        counts[slot_id] = counts.get(slot_id, 0) + 1
    return counts


def _get_village_effective_levels(api, village_id: int) -> tuple[dict[int, int], dict[int, int]]:
    """
    Return (effective_levels, queued_counts).
    effective_levels = current visible level + queued upgrades on the same slot.
    """
    levels = _get_village_building_levels(api, village_id)
    queued_counts = _get_village_queued_upgrades(api, village_id)
    effective = {int(slot_id): int(level) for slot_id, level in levels.items()}
    for slot_id, queued in queued_counts.items():
        effective[slot_id] = int(effective.get(slot_id, 0)) + int(queued)
    return effective, queued_counts


def _save_debug_pages(api, village_id: int) -> list[str]:
    os.makedirs(BUILDING_DEBUG_DIR, exist_ok=True)
    saved_paths = []
    for page in ("dorf1.php", "dorf2.php"):
        url = f"{api.server_url}/{page}?newdid={village_id}"
        response = api.session.get(url)
        response.raise_for_status()
        path = os.path.join(BUILDING_DEBUG_DIR, f"{page.replace('.php', '')}_village_{village_id}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(response.text)
        saved_paths.append(path)
    return saved_paths


def _find_upgrade_url(api, village_id: int, slot_id: int) -> str | None:
    """Try to discover clickable upgrade URL for a slot."""
    build_url = f"{api.server_url}/build.php?id={slot_id}&newdid={village_id}"
    response = api.session.get(build_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    def _contains_any(text: str, words: tuple[str, ...]) -> bool:
        return any(w in text for w in words)

    def _extract_onclick_href(onclick: str) -> str | None:
        patterns = [
            r"(?:location\.href|window\.location(?:\.href)?)\s*=\s*['\"]([^'\"]+)['\"]",
            r"openWindow\(['\"]([^'\"]+)['\"]",
        ]
        for pattern in patterns:
            match = re.search(pattern, onclick or "")
            if match:
                return match.group(1)
        return None

    def _score_candidate(href: str, text: str, classes: str, elem_id: str) -> int:
        href_l = href.lower()
        text_l = text.lower()
        classes_l = classes.lower()
        elem_id_l = elem_id.lower()
        blob = " ".join([href_l, text_l, classes_l, elem_id_l])

        is_known_build_page = any(page in href_l for page in ("build.php", "dorf1.php", "dorf2.php"))
        if not is_known_build_page:
            return -1
        if f"id={slot_id}" not in href_l:
            return -1
        if _contains_any(blob, ("demolish", "destroy", "abbrechen", "cancel")):
            return -1

        score = 0
        if _contains_any(blob, ("upgrade", "improve", "contract", "build", "green")):
            score += 40
        if _contains_any(href_l, ("a=", "c=", "checksum", "gid=", "action=build")):
            score += 30
        if "newdid=" in href_l:
            score += 20
        if "action=build" in href_l:
            score += 20
        if "t=" in href_l and not _contains_any(href_l, ("a=", "c=", "checksum")):
            score -= 10
        return score

    candidates: list[tuple[int, str]] = []

    for elem in soup.find_all(["a", "button"]):
        text = elem.get_text(" ", strip=True)
        classes = " ".join(elem.get("class", []))
        elem_id = elem.get("id", "")

        href = elem.get("href")
        if href:
            score = _score_candidate(href, text, classes, elem_id)
            if score >= 0:
                candidates.append((score, urljoin(api.server_url, href)))

        data_href = elem.get("data-href")
        if data_href:
            score = _score_candidate(data_href, text, classes, elem_id)
            if score >= 0:
                candidates.append((score, urljoin(api.server_url, data_href)))

        onclick_href = _extract_onclick_href(elem.get("onclick", ""))
        if onclick_href:
            score = _score_candidate(onclick_href, text, classes, elem_id)
            if score >= 0:
                candidates.append((score, urljoin(api.server_url, onclick_href)))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    # Last resort: raw HTML regex for JS redirects to build.php with this slot id.
    regex = rf"(?:build\.php\?[^\"'<>]*\bid={slot_id}\b[^\"'<>]*)"
    for href in re.findall(regex, response.text, flags=re.IGNORECASE):
        if not _contains_any(href.lower(), ("demolish", "destroy", "abbrechen")):
            return urljoin(api.server_url, href)

    return None


def _save_upgrade_debug_page(api, village_id: int, slot_id: int) -> str:
    os.makedirs(BUILDING_DEBUG_DIR, exist_ok=True)
    debug_url = f"{api.server_url}/build.php?id={slot_id}&newdid={village_id}"
    response = api.session.get(debug_url)
    response.raise_for_status()
    path = os.path.join(BUILDING_DEBUG_DIR, f"build_slot_{slot_id}_village_{village_id}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(response.text)
    return path


def _extract_building_name_from_build_page(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("h1")
    if not title:
        return "unknown"
    text = title.get_text(" ", strip=True)
    text = re.sub(r"\b(level|niveau|stufe|nivel|livello)\s*\d+\b", "", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip().lower()


def _get_slot_building_name(api, village_id: int, slot_id: int) -> str:
    try:
        build_url = f"{api.server_url}/build.php?id={slot_id}&newdid={village_id}"
        response = api.session.get(build_url)
        response.raise_for_status()
        return _extract_building_name_from_build_page(response.text)
    except Exception:
        return "unknown"


def _create_targets_by_filter(api, village_id: int, building_query: str, min_level: int, max_level: int, target_level: int) -> list[dict]:
    levels = _get_village_building_levels(api, village_id)
    query = building_query.strip().lower()
    targets = []

    for slot_id, current_level in sorted(levels.items()):
        if current_level < min_level or current_level > max_level:
            continue
        if current_level >= target_level:
            continue
        building_name = _get_slot_building_name(api, village_id, slot_id)
        if query in building_name:
            targets.append({"slot_id": slot_id, "target_level": target_level})
            print(f"✅ Matched slot {slot_id}: '{building_name}' level {current_level} -> {target_level}")

    return targets


def _get_village_slot_catalog(api, village_id: int) -> list[dict]:
    """Return sorted slot catalog: slot id, building name, and current level."""
    levels = _get_village_building_levels(api, village_id)
    catalog = []
    for slot_id in sorted(levels.keys()):
        catalog.append(
            {
                "slot_id": slot_id,
                "level": levels[slot_id],
                "name": _get_slot_building_name(api, village_id, slot_id),
            }
        )
    return catalog


def create_or_update_building_plan(api):
    villages = load_villages_from_identity()
    if not villages:
        print("❌ No villages found in identity. Run identity setup first.")
        return

    print("\n🏗️ Building Plan Setup")
    for idx, village in enumerate(villages):
        print(f"[{idx}] {village['village_name']} (ID: {village['village_id']})")

    try:
        choice = int(input("\nSelect village: ").strip())
        village = villages[choice]
    except (ValueError, IndexError):
        print("❌ Invalid village selection.")
        return

    village_id = village["village_id"]
    plan_file = _get_plan_file(village_id)
    existing = []
    if os.path.exists(plan_file):
        with open(plan_file, "r", encoding="utf-8") as f:
            existing = json.load(f).get("targets", [])

    print("\nCurrent plan targets:")
    if not existing:
        print("- (empty)")
    else:
        for t in existing:
            print(f"- slot {t['slot_id']} -> level {t['target_level']}")

    print("\n📋 Current slots in this village:")
    slot_catalog = _get_village_slot_catalog(api, village_id)
    if not slot_catalog:
        print("❌ No slots detected from page parsing.")
        debug_paths = _save_debug_pages(api, village_id)
        print("🛠️ Saved debug pages:")
        for p in debug_paths:
            print(f"- {p}")
        print("Try again after opening both village views in browser once, then rerun.")
        return

    for item in slot_catalog:
        print(f"- slot {item['slot_id']:>2}: {item['name']} (level {item['level']})")

    print("\nHow do you want to create targets?")
    print("[1] Manual slot list")
    print("[2] Auto by building name + current level range")
    mode = input("Select mode: ").strip()

    targets = []
    if mode == "1":
        print("\nEnter new targets. Leave slot blank to finish.")
        while True:
            slot_input = input("Slot ID (e.g. 1, 19, 26): ").strip()
            if not slot_input:
                break
            level_input = input("Target level: ").strip()
            try:
                slot_id = int(slot_input)
                target_level = int(level_input)
                if slot_id <= 0 or target_level <= 0:
                    raise ValueError
                targets.append({"slot_id": slot_id, "target_level": target_level})
            except ValueError:
                print("❌ Please enter positive integer values.")
    elif mode == "2":
        try:
            print("\nExample: building='cranny', min=1, max=9, target=10")
            building_query = input("Building name contains: ").strip()
            min_level = int(input("Current level minimum (X): ").strip())
            max_level = int(input("Current level maximum (Y): ").strip())
            target_level = int(input("Target level: ").strip())
            if not building_query:
                print("❌ Building name cannot be empty.")
                return
            if min_level < 0 or max_level < min_level or target_level <= 0:
                print("❌ Invalid numeric range.")
                return
            targets = _create_targets_by_filter(
                api=api,
                village_id=village_id,
                building_query=building_query,
                min_level=min_level,
                max_level=max_level,
                target_level=target_level,
            )
        except ValueError:
            print("❌ Please enter valid integer values.")
            return
    else:
        print("❌ Invalid setup mode.")
        return

    if not targets:
        print("ℹ️ No targets were generated, keeping existing file unchanged.")
        return

    payload = {
        "village_id": village_id,
        "village_name": village["village_name"],
        "targets": targets,
    }
    with open(plan_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
    print(f"✅ Building plan saved: {plan_file}")


def run_building_plan_once(api):
    villages = load_villages_from_identity()
    if not villages:
        print("❌ No villages found in identity.")
        return

    print("\n🏗️ Run Building Plan (single pass)")
    actions_taken = 0

    for village in villages:
        village_id = village["village_id"]
        plan_file = _get_plan_file(village_id)
        if not os.path.exists(plan_file):
            continue

        with open(plan_file, "r", encoding="utf-8") as f:
            plan = json.load(f)

        targets = sorted(plan.get("targets", []), key=lambda t: t["slot_id"])
        if not targets:
            continue

        print(f"\n🏘️ {village['village_name']} (ID: {village_id})")
        effective_levels, queued_counts = _get_village_effective_levels(api, village_id)

        for target in targets:
            slot_id = int(target["slot_id"])
            desired = int(target["target_level"])
            queued = int(queued_counts.get(slot_id, 0))
            effective_current = int(effective_levels.get(slot_id, 0))

            if effective_current >= desired:
                if queued > 0:
                    base_level = effective_current - queued
                    print(
                        f"- slot {slot_id}: current {base_level} (+{queued} queued), "
                        f"target {desired} ✅"
                    )
                else:
                    print(f"- slot {slot_id}: current {effective_current}, target {desired} ✅")
                continue

            if queued > 0:
                base_level = effective_current - queued
                print(
                    f"- slot {slot_id}: current {base_level} (+{queued} queued), "
                    f"target {desired} -> trying upgrade..."
                )
            else:
                print(f"- slot {slot_id}: current {effective_current}, target {desired} -> trying upgrade...")
            upgrade_url = _find_upgrade_url(api, village_id, slot_id)
            if not upgrade_url:
                print(f"  ❌ No upgrade action found (busy queue, missing resources, or wrong slot).")
                try:
                    debug_path = _save_upgrade_debug_page(api, village_id, slot_id)
                    print(f"  🛠️ Debug page saved: {debug_path}")
                except Exception as e:
                    print(f"  ⚠️ Could not save debug page: {e}")
                break

            response = api.session.get(upgrade_url, allow_redirects=True)
            if response.status_code >= 400:
                print(f"  ❌ Upgrade request failed with status {response.status_code}.")
                break

            print("  ✅ Upgrade request sent.")
            actions_taken += 1
            # One request at a time is safer; queue limits often block additional ones.
            break

    if actions_taken == 0:
        print("\nℹ️ No upgrade was started in this pass.")
    else:
        print(f"\n✅ Started {actions_taken} upgrade action(s).")
