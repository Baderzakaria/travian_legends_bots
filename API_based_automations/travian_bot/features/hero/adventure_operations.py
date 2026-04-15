import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.hero_manager import HeroManager


def _collect_adventure_links(api) -> list[str]:
    candidate_pages = [
        f"{api.server_url}/hero/adventures",
        f"{api.server_url}/hero",
    ]
    links = []

    for page_url in candidate_pages:
        response = api.session.get(page_url)
        if response.status_code >= 400:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            text = anchor.get_text(" ", strip=True).lower()
            classes = " ".join(anchor.get("class", [])).lower()
            title = (anchor.get("title") or "").lower()
            haystack = f"{href} {text} {classes} {title}"

            if "adventure" in haystack or ("build.php" in href and "eventType" in href):
                links.append(urljoin(api.server_url, href))

        # Buttons with onclick links.
        for node in soup.find_all(["button", "a"]):
            onclick = node.get("onclick", "")
            match = re.search(r"(?:location\.href|window\.location)\s*=\s*['\"]([^'\"]+)['\"]", onclick)
            if not match:
                continue
            href = match.group(1)
            haystack = href.lower()
            if "adventure" in haystack or ("build.php" in haystack and "eventtype" in haystack):
                links.append(urljoin(api.server_url, href))

    # Deduplicate while preserving order.
    seen = set()
    deduped = []
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        deduped.append(link)
    return deduped


def run_adventure_once(api):
    """
    Attempt to start one adventure for hero.
    Uses server-provided adventure links and validates by checking mission status.
    """
    hero_manager = HeroManager(api)
    status = hero_manager.fetch_hero_status()
    if not status:
        print("❌ Could not fetch hero status.")
        return False
    if not status.is_present:
        print("❌ Hero is not present.")
        return False
    if status.is_on_mission:
        print("ℹ️ Hero already on a mission.")
        return False
    if status.health is not None and status.health < 20:
        print(f"ℹ️ Hero health too low ({status.health}%).")
        return False

    links = _collect_adventure_links(api)
    if not links:
        print("ℹ️ No adventure links found right now.")
        return False

    print(f"🧭 Found {len(links)} adventure link candidates.")
    for link in links:
        try:
            response = api.session.get(link, allow_redirects=True)
            if response.status_code >= 400:
                continue
            # Re-check status after each attempt.
            new_status = hero_manager.fetch_hero_status()
            if new_status and new_status.is_on_mission:
                print(f"✅ Adventure started via: {link}")
                return True
        except Exception:
            continue

    print("❌ Could not start adventure from discovered links.")
    return False
