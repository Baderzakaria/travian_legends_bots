import os
import time
from urllib.parse import urlparse

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def _make_driver(headless: bool = False):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--window-size=1365,900")
    return webdriver.Chrome(options=options)


def _click_if_present(driver, xpath: str, timeout: int = 4) -> bool:
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
        element.click()
        return True
    except Exception:
        return False


def _dismiss_cookie_popup_if_any(driver) -> None:
    xpaths = [
        "//button[contains(., 'Accept all')]",
        "//button[contains(., 'I agree')]",
        "//button[contains(., 'Accept')]",
        "//button[contains(., 'Agree')]",
        "//button[contains(., 'OK')]",
    ]
    for xpath in xpaths:
        if _click_if_present(driver, xpath, timeout=2):
            time.sleep(0.6)
            return


def _dismiss_adventure_top_blocks(driver, preserve_video_cta: bool = False) -> None:
    close_xpaths = [
        "//button[contains(@class,'close')]",
        "//a[contains(@class,'close')]",
        "//button[@aria-label='Close']",
        "//button[@title='Close']",
        "//button[normalize-space()='x']",
        "//button[normalize-space()='X']",
        "//button[normalize-space()='*']",
    ]
    for xpath in close_xpaths:
        for _ in range(3):
            if not _click_if_present(driver, xpath, timeout=1):
                break
            time.sleep(0.3)

    driver.execute_script(
        """
        const preserveVideoCta = arguments[0];
        const nodes = [...document.querySelectorAll('*')];
        for (const n of nodes) {
          const s = window.getComputedStyle(n);
          if (!s) continue;
          const z = parseInt(s.zIndex || '0', 10);
          const isFixed = s.position === 'fixed' || s.position === 'sticky';
          const txt = (n.innerText || '').toLowerCase();
          const cls = (n.className || '').toString().toLowerCase();
          if (isFixed && z >= 1000) {
            if (!preserveVideoCta || (!txt.includes('watch video') && !cls.includes('videofeature'))) {
              n.style.display = 'none';
            }
            continue;
          }
          if (!preserveVideoCta && (txt.includes('watch video') || cls.includes('videofeature'))) {
            if (n.offsetHeight < 260) n.style.display = 'none';
          }
        }
        """,
        preserve_video_cta,
    )


def _inject_session_cookies(driver, server_url: str, session_cookies: dict) -> None:
    parsed = urlparse(server_url)
    domain = parsed.hostname or ""
    driver.get(f"{parsed.scheme}://{domain}/")
    for name, value in session_cookies.items():
        try:
            driver.add_cookie(
                {
                    "name": str(name),
                    "value": str(value),
                    "domain": domain,
                    "path": "/",
                }
            )
        except Exception:
            continue


def _is_adblock_detected(driver) -> bool:
    patterns = [
        "disable adblock",
        "disable your adblock",
        "ad blocker",
        "adblock detected",
        "please disable adblock",
    ]
    page = driver.page_source.lower()
    return any(p in page for p in patterns)


def _center_click_video_surface(driver) -> bool:
    candidates = []
    for css in ["video", "iframe", ".videoFeature", ".video", ".videofeature"]:
        try:
            candidates.extend(driver.find_elements(By.CSS_SELECTOR, css))
        except Exception:
            continue

    ranked = []
    for elem in candidates:
        try:
            if not elem.is_displayed():
                continue
            rect = elem.rect or {}
            area = float(rect.get("width", 0)) * float(rect.get("height", 0))
            if area < 20000:
                continue
            ranked.append((area, elem))
        except Exception:
            continue
    ranked.sort(key=lambda x: x[0], reverse=True)

    for _, elem in ranked[:3]:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            time.sleep(0.2)
            rect = elem.rect
            cx = rect["x"] + rect["width"] / 2
            cy = rect["y"] + rect["height"] / 2
            driver.execute_script(
                "const x=arguments[0], y=arguments[1];"
                "const el=document.elementFromPoint(x,y); if(el){el.click();}",
                cx,
                cy,
            )
            time.sleep(0.6)
            return True
        except Exception:
            continue

    try:
        size = driver.get_window_size()
        cx = int(size["width"] * 0.5)
        cy = int(size["height"] * 0.5)
        actions = ActionChains(driver)
        actions.move_by_offset(cx, cy).click().perform()
        actions.move_by_offset(-cx, -cy).perform()
        time.sleep(0.6)
        return True
    except Exception:
        return False


def _click_inside_visible_iframe(driver) -> bool:
    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        return False
    for frame in frames:
        try:
            if not frame.is_displayed():
                continue
            rect = frame.rect or {}
            if float(rect.get("width", 0)) * float(rect.get("height", 0)) < 20000:
                continue
            driver.switch_to.frame(frame)
            try:
                body = driver.find_element(By.TAG_NAME, "body")
                body.click()
                time.sleep(0.5)
                return True
            finally:
                driver.switch_to.default_content()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            continue
    return False


def _close_extra_tabs(driver, keep_handles: set[str]) -> None:
    """Close any unexpected extra tabs/windows and switch back to a kept handle."""
    try:
        handles = list(driver.window_handles)
    except Exception:
        return
    for h in handles:
        if h in keep_handles:
            continue
        try:
            driver.switch_to.window(h)
            driver.close()
        except Exception:
            pass
    for h in keep_handles:
        try:
            driver.switch_to.window(h)
            return
        except Exception:
            continue


def _click_video_start_button_and_wait(driver, wait_seconds: int = 40) -> bool:
    """
    Click the in-popup video start/play button (usually centered), then wait.
    """
    start_xpaths = [
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'play')]",
        "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')]",
        "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'play')]",
        "//*[@role='button' and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start')]",
        "//*[@role='button' and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'play')]",
    ]

    for xpath in start_xpaths:
        try:
            elem = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            elem.click()
            print(f"[video] Clicked popup start/play button. Waiting {wait_seconds}s...")
            time.sleep(wait_seconds)
            return True
        except Exception:
            continue

    # Fallback: click center-most large visible button-like element.
    try:
        clicked = driver.execute_script(
            """
            const vw = window.innerWidth || document.documentElement.clientWidth;
            const vh = window.innerHeight || document.documentElement.clientHeight;
            const cx = vw / 2;
            const cy = vh / 2;
            const nodes = [...document.querySelectorAll('button, [role="button"], a, div')];
            let best = null;
            let bestScore = Infinity;
            for (const n of nodes) {
              const r = n.getBoundingClientRect();
              if (!r || r.width < 40 || r.height < 30) continue;
              const style = window.getComputedStyle(n);
              if (!style || style.display === 'none' || style.visibility === 'hidden') continue;
              const txt = (n.innerText || '').toLowerCase();
              const cls = (n.className || '').toString().toLowerCase();
              if (!(txt.includes('start') || txt.includes('play') || cls.includes('play') || cls.includes('start'))) continue;
              const nx = r.left + r.width / 2;
              const ny = r.top + r.height / 2;
              const dist = Math.hypot(nx - cx, ny - cy);
              if (dist < bestScore) {
                bestScore = dist;
                best = n;
              }
            }
            if (best) {
              best.click();
              return true;
            }
            return false;
            """
        )
        if clicked:
            print(f"[video] Clicked centered popup start/play control. Waiting {wait_seconds}s...")
            time.sleep(wait_seconds)
            return True
    except Exception:
        pass

    # Aggressive fallback for play overlays without text/classes (triangle icon only).
    try:
        clicked_any = False
        for _ in range(4):
            if _click_inside_visible_iframe(driver):
                clicked_any = True
            if _center_click_video_surface(driver):
                clicked_any = True
            try:
                center_clicked = driver.execute_script(
                    """
                    const vw = window.innerWidth || document.documentElement.clientWidth;
                    const vh = window.innerHeight || document.documentElement.clientHeight;
                    const x = Math.floor(vw * 0.5);
                    const y = Math.floor(vh * 0.5);
                    const el = document.elementFromPoint(x, y);
                    if (!el) return false;
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, clientX:x, clientY:y}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, clientX:x, clientY:y}));
                    el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, clientX:x, clientY:y}));
                    return true;
                    """
                )
                if center_clicked:
                    clicked_any = True
            except Exception:
                pass
            time.sleep(0.7)

        if clicked_any:
            print(f"[video] Clicked fallback video center. Waiting {wait_seconds}s...")
            time.sleep(wait_seconds)
            return True
    except Exception:
        pass

    return False


def _wait_for_video_reward_or_timeout(
    driver,
    timeout_s: int = 120,
    before_watch_count: int | None = None,
    keep_handles: set[str] | None = None,
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if keep_handles:
            _close_extra_tabs(driver, keep_handles)

        if _is_adblock_detected(driver):
            print("[video] Adblock-like blocking detected. Disable adblock for reward videos.")
            return False

        visible_watch_nodes = _get_visible_watch_video_buttons(driver)
        visible_count = len(visible_watch_nodes)
        if before_watch_count is not None and visible_count < before_watch_count:
            print(
                "[video] Reward appears completed "
                f"(Watch video count dropped {before_watch_count} -> {visible_count})."
            )
            return True
        if before_watch_count is None and visible_count == 0:
            print("[video] Reward appears completed (Watch video no longer visible).")
            return True

        _click_inside_visible_iframe(driver)
        _center_click_video_surface(driver)
        time.sleep(3)

    print("[video] Timed out waiting for video reward completion.")
    return False


def _handle_video_popup_window(
    driver,
    parent_handle: str,
    timeout_s: int = 150,
    before_watch_count: int | None = None,
) -> None:
    popup_handle = None
    start = time.time()
    while time.time() - start < 12:
        handles = driver.window_handles
        for h in handles:
            if h != parent_handle:
                popup_handle = h
                break
        if popup_handle:
            break
        time.sleep(0.4)

    if not popup_handle:
        print("[video] No separate popup window detected. Using same-page flow.")
        _click_video_start_button_and_wait(driver, wait_seconds=40)
        _wait_for_video_reward_or_timeout(
            driver,
            timeout_s=timeout_s,
            before_watch_count=before_watch_count,
            keep_handles={parent_handle},
        )
        return

    print("[video] Popup window detected. Switching to popup.")
    try:
        driver.switch_to.window(popup_handle)
        _click_video_start_button_and_wait(driver, wait_seconds=40)
        popup_deadline = time.time() + timeout_s
        while time.time() < popup_deadline:
            _close_extra_tabs(driver, {parent_handle, popup_handle})
            if _is_adblock_detected(driver):
                print("[video] Adblock-like message detected inside popup.")
                break
            _click_inside_visible_iframe(driver)
            _center_click_video_surface(driver)

            # If popup closes itself, this call may fail or handle disappears.
            handles = driver.window_handles
            if popup_handle not in handles:
                print("[video] Popup closed.")
                break
            time.sleep(3)
    except Exception as e:
        print(f"[video] Popup handling warning: {e}")
    finally:
        try:
            if popup_handle in driver.window_handles:
                driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(parent_handle)
        except Exception:
            pass
        _wait_for_video_reward_or_timeout(
            driver,
            timeout_s=20,
            before_watch_count=before_watch_count,
            keep_handles={parent_handle},
        )


def _click_watch_video_button(driver) -> bool:
    # Prefer the first visible watch-video CTA in adventures (usually duration bonus).
    try:
        buttons = driver.find_elements(By.XPATH, "//button[contains(., 'Watch video')]")
    except Exception:
        buttons = []
    visible = [b for b in buttons if b.is_displayed()]
    for btn in visible:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.2)
            btn.click()
            return True
        except Exception:
            continue
    return _click_if_present(driver, "//button[contains(., 'Watch video')]", timeout=4)


def _get_visible_watch_video_buttons(driver):
    try:
        buttons = driver.find_elements(By.XPATH, "//button[contains(., 'Watch video')]")
    except Exception:
        return []
    return [b for b in buttons if b.is_displayed()]


def _close_video_modal_if_any(driver) -> None:
    xpaths = [
        "//button[contains(@class,'close')]",
        "//a[contains(@class,'close')]",
        "//button[@aria-label='Close']",
        "//button[@title='Close']",
        "//button[contains(., 'Close')]",
        "//button[normalize-space()='x']",
        "//button[normalize-space()='X']",
    ]
    for xpath in xpaths:
        if _click_if_present(driver, xpath, timeout=1):
            time.sleep(0.4)
            return


def _is_watch_video_bonus_already_active(driver) -> bool:
    """
    Detect when reward-video bonuses are already active so we don't keep watching videos.
    """
    try:
        page = (driver.page_source or "").lower()
    except Exception:
        return False

    markers = (
        "active for next adventure",
        "active for next normal adventure",
    )
    return any(marker in page for marker in markers)


def _watch_video_rewards_in_order(driver, adventures_url: str, max_videos: int = 2) -> int:
    watched = 0
    for idx in range(max_videos):
        if _is_watch_video_bonus_already_active(driver):
            print("[video] Bonus already active for next adventure; skipping further watch-video actions.")
            break

        visible_before = _get_visible_watch_video_buttons(driver)
        if not visible_before:
            break

        before_count = len(visible_before)
        print(f"[video] Found {before_count} Watch video button(s) before step {idx + 1}.")

        clicked = False
        for btn in visible_before:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.2)
                btn.click()
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            print("[video] Could not click Watch video button.")
            break

        print("Clicked 'Watch video'. Waiting for ad to complete...")
        parent_handle = driver.current_window_handle
        _handle_video_popup_window(
            driver,
            parent_handle=parent_handle,
            timeout_s=150,
            before_watch_count=before_count,
        )

        # Reward popups are sometimes modal overlays in the same page.
        _close_video_modal_if_any(driver)
        time.sleep(1)

        # Reload adventures page so UI state updates, then continue with next reward button.
        driver.get(adventures_url)
        _dismiss_cookie_popup_if_any(driver)
        _dismiss_adventure_top_blocks(driver, preserve_video_cta=True)
        time.sleep(1.5)

        if _is_watch_video_bonus_already_active(driver):
            watched += 1
            print("[video] Bonus status is active after video; stopping additional watch-video actions.")
            break

        visible_after = _get_visible_watch_video_buttons(driver)
        if len(visible_after) < before_count:
            watched += 1
        else:
            # Keep progress optimistic if the button changed state but count did not.
            watched += 1
            print("[video] Watch video count did not drop; continuing with next check.")
    return watched


def run_adventure_browser_once(
    server_url: str,
    watch_video_first: bool = False,
    headless: bool = False,
    session_cookies: dict | None = None,
) -> bool:
    """
    Browser automation fallback for hero adventures.
    Opens /hero/adventures, optionally performs strict watch-video flow,
    then clicks first available Explore button.
    """
    load_dotenv()
    email = os.getenv("TRAVIAN_EMAIL")
    password = os.getenv("TRAVIAN_PASSWORD")
    if not email or not password:
        print("Missing TRAVIAN_EMAIL / TRAVIAN_PASSWORD in .env")
        return False

    if watch_video_first and headless:
        print("[video] Forcing headed browser mode (headless often blocks ad playback/reward).")
        headless = False

    driver = None
    try:
        driver = _make_driver(headless=headless)
        wait = WebDriverWait(driver, 30)

        if session_cookies:
            _inject_session_cookies(driver, server_url, session_cookies)
            time.sleep(1)
        else:
            driver.get("https://www.travian.com/international#loginLobby")
            _dismiss_cookie_popup_if_any(driver)
            email_field = wait.until(EC.presence_of_element_located((By.NAME, "email")))
            password_field = wait.until(EC.presence_of_element_located((By.NAME, "password")))
            email_field.clear()
            email_field.send_keys(email)
            password_field.clear()
            password_field.send_keys(password)
            submit = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']")))
            submit.click()
            time.sleep(6)

        adventures_url = f"{server_url.rstrip('/')}/hero/adventures"
        driver.get(adventures_url)
        _dismiss_cookie_popup_if_any(driver)
        _dismiss_adventure_top_blocks(driver, preserve_video_cta=watch_video_first)
        time.sleep(2)

        if watch_video_first:
            if _is_adblock_detected(driver):
                print("[video] Adblock-like blocking detected before playback. Disable adblock and retry.")

            if _is_watch_video_bonus_already_active(driver):
                print("[video] Bonus already active on page; skipping watch-video step.")
            else:
                watched_count = _watch_video_rewards_in_order(
                    driver=driver,
                    adventures_url=adventures_url,
                    max_videos=2,
                )
                if watched_count > 0:
                    print(f"[video] Completed {watched_count} watch-video step(s) before Explore.")
                else:
                    print("Watch video button not directly clickable, continuing to Explore.")

            # Refresh adventure page after ad flow to ensure Explore list/buttons are rendered again.
            driver.get(adventures_url)
            _dismiss_cookie_popup_if_any(driver)
            _dismiss_adventure_top_blocks(driver, preserve_video_cta=False)
            time.sleep(2)

        explore_xpaths = [
            "(//button[contains(., 'Explore')])[1]",
            "(//a[contains(., 'Explore')])[1]",
            "//button[contains(., 'Explore')]",
            "//a[contains(., 'Explore')]",
            "//button[contains(., 'Explorer')]",
            "//a[contains(., 'Explorer')]",
        ]
        for xpath in explore_xpaths:
            try:
                _dismiss_adventure_top_blocks(driver, preserve_video_cta=False)
                element = WebDriverWait(driver, 4).until(
                    EC.presence_of_element_located((By.XPATH, xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                time.sleep(0.2)
            except Exception:
                pass
            if _click_if_present(driver, xpath, timeout=8):
                print("Clicked Explore in browser.")
                time.sleep(2)
                return True

        print("Could not find clickable Explore action in browser.")
        return False

    except TimeoutException as e:
        print(f"Browser adventure timeout: {e}")
        return False
    except Exception as e:
        print(f"Browser adventure failed: {e}")
        return False
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
