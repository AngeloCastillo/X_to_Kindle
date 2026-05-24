import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime

from playwright.sync_api import sync_playwright

URL = "https://x.com/home?lang=es"
VIEWPORT_WIDTH = 1100
VIEWPORT_HEIGHT = 900

MAX_SCROLLS = 200
SCROLL_STEP = 600
SCROLL_DELAY = 2
INITIAL_WAIT = 5
MENU_WAIT_MS = 600
BETWEEN_ACTIONS_MS = 400

# ----------------------------------------------------------------------------
# >>> TUNE THE FILTER'S GOAL HERE <<<
# ----------------------------------------------------------------------------
# INTEREST_TOPICS  : tweets about these topics -> action "Follow @username"
# UNINTEREST_TOPICS: tweets about these topics -> action "Not interested in this post"
# Anything else stays as NEUTRAL (left untouched).
INTEREST_TOPICS = (
    "science, personal motivation, artificial intelligence, programming, "
    "mathematics, engineering or serious technical topics"
)
UNINTEREST_TOPICS = (
    "gossip, celebrities, partisan politics, sex, sexual content, "
    "trivialities, personal drama, religion or unproductive/trivial content"
)

# Stop the script after classifying this many tweets as INTEREST.
# Set to 0 to disable (run until MAX_SCROLLS is reached).
STOP_AFTER_INTEREST = 30
# ----------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(SCRIPT_DIR, "browser_profile_chrome")
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
LOG_PATH = os.path.join(SCRIPT_DIR, "x_filter_log.txt")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a tweet classifier. You will receive the text of a tweet (which may be "
    "in any language) and must reply with EXACTLY one word, in uppercase, with no "
    "explanation:\n"
    f"- INTEREST   -> if the tweet is about {INTEREST_TOPICS}.\n"
    f"- UNINTEREST -> if the tweet is about {UNINTEREST_TOPICS}.\n"
    "- NEUTRAL    -> for anything else, including ambiguous or very short tweets.\n"
    "Reply with only the single word. No punctuation, no quotes, no commentary."
)


def load_env(path: str) -> dict:
    env = {}
    if not os.path.isfile(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def classify(text: str, api_key: str, model: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:1500]},
        ],
        "max_tokens": 2048,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        CEREBRAS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "x-filter/1.0 (+python)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        out = (
            msg.get("content")
            or msg.get("reasoning_content")
            or msg.get("reasoning")
            or ""
        )
        if not out:
            print(f"  [!] Cerebras unexpected response: {json.dumps(data)[:400]}")
        out = out.strip().upper()
        for tag in ("UNINTEREST", "INTEREST", "NEUTRAL"):
            if tag in out:
                return tag
        return "NEUTRAL"
    except urllib.error.HTTPError as e:
        print(f"  [!] Cerebras HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
        return "NEUTRAL"
    except Exception as e:
        print(f"  [!] Cerebras error: {e}")
        return "NEUTRAL"


def remove_ads(page) -> int:
    return page.evaluate(
        """
        () => {
            const articles = Array.from(document.querySelectorAll('article'));
            let n = 0;
            for (const a of articles) {
                const hasAd = Array.from(a.querySelectorAll('span'))
                    .some(s => s.textContent.trim() === 'Anuncio');
                if (hasAd) { a.remove(); n++; }
            }
            return n;
        }
        """
    )


def collect_tweets(page) -> list:
    return page.evaluate(
        """
        () => {
            const arts = Array.from(document.querySelectorAll('article'));
            const out = [];
            for (const a of arts) {
                const t = a.querySelector('[data-testid="tweetText"]');
                if (!t) continue;
                const text = t.innerText.trim();
                if (!text) continue;
                const statusLink = a.querySelector('a[href*="/status/"]');
                if (!statusLink) continue;
                const href = statusLink.getAttribute('href');
                const m = href.match(/^\\/([^\\/]+)\\/status\\/(\\d+)/);
                if (!m) continue;
                const username = m[1];
                const statusId = m[2];
                out.push({ statusId, username, text });
            }
            return out;
        }
        """
    )


def open_more_options(page, status_id: str) -> bool:
    ok = page.evaluate(
        """
        (sid) => {
            const arts = Array.from(document.querySelectorAll('article'));
            for (const a of arts) {
                const link = a.querySelector(`a[href*="/status/${sid}"]`);
                if (!link) continue;
                a.scrollIntoView({block: 'center', behavior: 'instant'});
                const btn = a.querySelector('button[aria-label="Más opciones"]');
                if (!btn) return false;
                btn.click();
                return true;
            }
            return false;
        }
        """,
        status_id,
    )
    if ok:
        page.wait_for_timeout(MENU_WAIT_MS)
    return ok


def click_menu_item(page, prefixes: list) -> str:
    return page.evaluate(
        """
        (prefixes) => {
            const items = Array.from(document.querySelectorAll('[role="menuitem"]'));
            for (const m of items) {
                const t = (m.innerText || '').trim();
                for (const p of prefixes) {
                    if (t === p || t.startsWith(p)) {
                        m.click();
                        return t;
                    }
                }
            }
            return null;
        }
        """,
        prefixes,
    )


def close_any_menu(page) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def log_action(category: str, username: str, text: str, menu_text: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {category} @{username} | menu={menu_text!r} | {text}\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    print(f"  -> logged: {category} @{username}")


def main() -> None:
    env = load_env(ENV_PATH)
    api_key = env.get("CEREBRAS_API_KEY")
    model = env.get("CEREBRAS_MODEL", "gpt-oss-120b")
    if not api_key:
        print("ERROR: CEREBRAS_API_KEY not found in .env")
        sys.exit(1)
    print(f"[i] Cerebras model: {model}")

    if not os.path.isdir(USER_DATA_DIR):
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        print(f"[!] No saved profile. You will need to log in.")

    print(f"[i] Opening {URL}")
    with sync_playwright() as p:
        launch_kwargs = dict(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            device_scale_factor=1.0,
            user_agent=USER_AGENT,
            locale="es-ES",
            color_scheme="dark",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--no-first-run",
            ],
            ignore_default_args=["--enable-automation"],
        )
        try:
            context = p.chromium.launch_persistent_context(
                channel="chrome", **launch_kwargs
            )
            print("[i] Using installed Google Chrome.")
        except Exception as e:
            print(f"[!] Chrome failed ({e}). Falling back to bundled Chromium.")
            context = p.chromium.launch_persistent_context(**launch_kwargs)

        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        time.sleep(INITIAL_WAIT)

        processed = set()
        interest_count = 0
        last_y = -1
        stagnant = 0
        scrolls = 0
        stop_flag = False

        while scrolls < MAX_SCROLLS and not stop_flag:
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            ads = remove_ads(page)
            if ads:
                print(f"[ads] removed {ads}")

            tweets = collect_tweets(page)
            for t in tweets:
                sid = t["statusId"]
                if sid in processed:
                    continue
                processed.add(sid)
                text = t["text"]
                user = t["username"]
                print(f"\n@{user} :: {text[:120].replace(chr(10), ' ')}")

                category = classify(text, api_key, model)
                print(f"  -> {category}")

                if category == "UNINTEREST":
                    if open_more_options(page, sid):
                        clicked = click_menu_item(page, ["No me interesa este post"])
                        if clicked:
                            log_action("UNINTEREST", user, text, clicked)
                        else:
                            print("  [!] 'No me interesa' menu item not found")
                            close_any_menu(page)
                    page.wait_for_timeout(BETWEEN_ACTIONS_MS)
                elif category == "INTEREST":
                    if open_more_options(page, sid):
                        clicked = click_menu_item(page, [f"Seguir a @{user}", "Seguir a "])
                        if clicked:
                            log_action("INTEREST", user, text, clicked)
                        else:
                            print("  [!] 'Seguir a' menu item not found (maybe already following)")
                            close_any_menu(page)
                    page.wait_for_timeout(BETWEEN_ACTIONS_MS)
                    interest_count += 1
                    if STOP_AFTER_INTEREST and interest_count >= STOP_AFTER_INTEREST:
                        print(f"\n[i] Reached STOP_AFTER_INTEREST={STOP_AFTER_INTEREST}. Stopping.")
                        stop_flag = True
                        break

            page.evaluate(f"window.scrollBy(0, {SCROLL_STEP})")
            scrolls += 1
            time.sleep(SCROLL_DELAY)

            y = page.evaluate("window.pageYOffset")
            if y == last_y:
                stagnant += 1
                if stagnant >= 3:
                    print("[i] No more new content. Stopping.")
                    break
            else:
                stagnant = 0
            last_y = y

        print("\n[i] Done. Press ENTER to close the browser.")
        try:
            input()
        except EOFError:
            pass
        context.close()


if __name__ == "__main__":
    main()
