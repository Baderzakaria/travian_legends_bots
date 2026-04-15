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


def _wait_for_video_reward_or_timeout(driver, timeout_s: int = 120) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _is_adblock_detected(driver):
            print("[video] Adblock-like blocking detected. Disable adblock for reward videos.")
            return False

        watch_nodes = driver.find_elements(By.XPATH, "//button[contains(., 'Watch video')]")
        visible_watch_nodes = [n for n in watch_nodes if n.is_displayed()]
        if len(visible_watch_nodes) == 0:
            print("[video] Reward appears completed (Watch video no longer visible).")
            return True

        _click_inside_visible_iframe(driver)
        _center_click_video_surface(driver)
        time.sleep(3)

    print("[video] Timed out waiting for video reward completion.")
    return False


def _handle_video_popup_window(driver, parent_handle: str, timeout_s: int = 150) -> None:
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
        _wait_for_video_reward_or_timeout(driver, timeout_s=timeout_s)
        return

    print("[video] Popup window detected. Switching to popup.")
    try:
        driver.switch_to.window(popup_handle)
        popup_deadline = time.time() + timeout_s
        while time.time() < popup_deadline:
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
            parent_handle = driver.current_window_handle
            clicked_video = _click_watch_video_button(driver)
            if clicked_video:
                print("Clicked 'Watch video'. Waiting for ad to complete...")
                _handle_video_popup_window(driver, parent_handle=parent_handle, timeout_s=150)
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
