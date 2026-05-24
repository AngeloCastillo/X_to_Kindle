import os
import sys
import time
import smtplib
from email.message import EmailMessage
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow")
    sys.exit(1)

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF is required. Install with: pip install PyMuPDF")
    sys.exit(1)

URL = "https://x.com/home?lang=es"
MAX_SCROLLS = 5
SCROLL_DELAY = 2
POST_LOAD_WAIT = 2
INITIAL_WAIT = 5
VIEWPORT_WIDTH = 450
VIEWPORT_HEIGHT = 700
SCROLL_OVERLAP_PX = 40
TEXT_SCALE = 0.85
PDF_JPEG_QUALITY = 95
PDF_RESOLUTION = 150
LINK_BADGE_W = 26
LINK_BADGE_H = 26
LINK_BADGE_MARGIN = 6

USE_CHROME = True
_PROFILE_NAME = "browser_profile_chrome" if USE_CHROME else "browser_profile_chromium"
USER_DATA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), _PROFILE_NAME
)
FORCE_LOGIN = False
LOGIN_URL = "https://x.com/i/flow/login"
LOGIN_URL_HINTS = ("/login", "/i/flow/login", "/account/access", "signin", "sign_in")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def build_output_basename(url: str) -> str:
    domain = urlparse(url).netloc or "page"
    domain = domain.replace("www.", "").replace(":", "_")
    timestamp = datetime.now().strftime("%y-%m-%d_%H-%M")
    return f"{domain}_{timestamp}"


def remove_ads(page) -> int:
    return page.evaluate(
        """
        () => {
            const articles = Array.from(document.querySelectorAll('article'));
            let n = 0;
            for (const a of articles) {
                const hasAd = Array.from(a.querySelectorAll('span'))
                    .some(s => s.textContent.trim() === 'Anuncio');
                if (hasAd) {
                    a.remove();
                    n++;
                }
            }
            return n;
        }
        """
    )


def expand_show_more(page) -> int:
    clicks = page.evaluate(
        """
        () => {
            const spans = Array.from(document.querySelectorAll('span'));
            let n = 0;
            for (const s of spans) {
                if (s.textContent.trim() !== 'Mostrar más') continue;
                if (s.closest('a')) continue;
                const r = s.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                s.click();
                n++;
            }
            return n;
        }
        """
    )
    return clicks


def scroll_and_capture(page) -> list:
    from io import BytesIO

    real_w = int(page.evaluate("window.innerWidth"))
    real_h = int(page.evaluate("window.innerHeight"))
    print(f"[debug] Actual viewport: {real_w}x{real_h} (no_viewport mode)")

    screenshots = []
    last_position = -1
    stagnant = 0
    step = max(1, real_h - SCROLL_OVERLAP_PX)
    for i in range(MAX_SCROLLS):
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        scroll_y = page.evaluate("window.pageYOffset")
        doc_height = page.evaluate("document.documentElement.scrollHeight")
        inner_h = page.evaluate("window.innerHeight")
        inner_w = page.evaluate("window.innerWidth")
        url = page.url

        print(f"\n--- Capture #{i+1} ---")
        print(f"  URL              : {url}")
        print(f"  window.pageYOffset = {scroll_y}px")
        print(f"  window.innerWidth  = {inner_w}px")
        print(f"  window.innerHeight = {inner_h}px")
        print(f"  document.scrollHeight = {doc_height}px")
        print(f"  page.screenshot(full_page=False, clip=None, type=png)")
        print(f"  -> captures the CURRENT viewport via Playwright CDP")
        print(f"     (Page.captureScreenshot on Chromium renderer)")

        try:
            ads = remove_ads(page)
            if ads > 0:
                print(f"  Removed {ads} ad article(s).")
        except Exception as e:
            print(f"  [!] remove_ads failed: {e}")

        try:
            n = expand_show_more(page)
            if n > 0:
                print(f"  Expanded {n} 'Mostrar más' span(s).")
                page.wait_for_timeout(300)
        except Exception as e:
            print(f"  [!] expand_show_more failed: {e}")

        tweets_data = page.evaluate(
            """
            () => {
                const arts = Array.from(document.querySelectorAll('article'));
                const out = [];
                const vw = window.innerWidth;
                const vh = window.innerHeight;
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
                    const ar = a.getBoundingClientRect();
                    if (ar.bottom < 0 || ar.top > vh || ar.right < 0 || ar.left > vw) continue;
                    const tr = t.getBoundingClientRect();
                    const fs = parseFloat(getComputedStyle(t).fontSize) || 14;
                    const lh = parseFloat(getComputedStyle(t).lineHeight);
                    out.push({
                        username: m[1],
                        statusId: m[2],
                        text: text,
                        articleX: ar.left,
                        articleY: ar.top,
                        articleW: ar.width,
                        articleH: ar.height,
                        textX: tr.left,
                        textY: tr.top,
                        textW: tr.width,
                        textH: tr.height,
                        fontSize: fs,
                        lineHeight: isFinite(lh) ? lh : fs * 1.3,
                    });
                }
                return out;
            }
            """
        )

        png_bytes = page.screenshot(full_page=False, type="png")
        img = Image.open(BytesIO(png_bytes))
        img.load()
        screenshots.append({"img": img, "tweets": tweets_data})
        print(f"  captured shot #{len(screenshots)} (size={img.size}, tweets={len(tweets_data)})")

        page.evaluate(f"window.scrollBy(0, {step})")
        new_position = page.evaluate("window.pageYOffset")
        print(f"  after scrollBy({step}): pageYOffset = {new_position}px")

        if new_position == last_position:
            stagnant += 1
            if stagnant >= 2:
                print(f"  No new content after scroll #{i+1}. Stopping.")
                break
        else:
            stagnant = 0
        last_position = new_position
    return screenshots


def screenshots_to_pdf(shots: list, pdf_path: str) -> None:
    from io import BytesIO
    if not shots:
        raise RuntimeError("No screenshots captured.")

    doc = fitz.open()
    for shot in shots:
        img = shot["img"].convert("L")
        w, h = img.size

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=PDF_JPEG_QUALITY, optimize=True)
        img_bytes = buf.getvalue()

        page = doc.new_page(width=w, height=h)
        page.insert_image(fitz.Rect(0, 0, w, h), stream=img_bytes)

        for t in shot.get("tweets", []):
            tx0 = max(0, t["textX"])
            ty0 = max(0, t["textY"])
            tx1 = min(w, t["textX"] + t["textW"])
            ty1 = min(h, t["textY"] + t["textH"])
            if tx1 > tx0 and ty1 > ty0:
                text_rect = fitz.Rect(tx0, ty0, tx1, ty1)
                fs = max(4.0, float(t.get("fontSize", 14)))
                try:
                    page.insert_textbox(
                        text_rect,
                        t["text"],
                        fontsize=fs,
                        color=(1, 1, 1),
                        render_mode=3,
                    )
                except Exception:
                    pass

            ax1 = t["articleX"] + t["articleW"]
            ay1 = t["articleY"] + t["articleH"]
            lx1 = min(w, ax1 - LINK_BADGE_MARGIN)
            ly1 = min(h, ay1 - LINK_BADGE_MARGIN)
            lx0 = max(0, lx1 - LINK_BADGE_W)
            ly0 = max(0, ly1 - LINK_BADGE_H)
            if lx1 <= lx0 or ly1 <= ly0:
                continue
            link_rect = fitz.Rect(lx0, ly0, lx1, ly1)

            url = f"https://x.com/{t['username']}/status/{t['statusId']}"
            page.insert_link({
                "kind": fitz.LINK_URI,
                "from": link_rect,
                "uri": url,
            })

    doc.save(pdf_path, garbage=4, deflate=True)
    doc.close()


def needs_login(page) -> bool:
    try:
        current_url = page.url.lower()
    except Exception:
        return False
    return any(hint in current_url for hint in LOGIN_URL_HINTS)


def interactive_login(url: str) -> None:
    print("\n" + "=" * 60)
    print(" LOGIN REQUIRED")
    print("=" * 60)
    print(f" A browser window is open at: {url}")
    print(" 1) Log in manually in that window.")
    print(" 2) When you're done and see your account logged in,")
    print("    come back to this console and press ENTER to continue.")
    print(" Your session will be saved for next time.")
    print("=" * 60)
    try:
        input(" Press ENTER once you're logged in... ")
    except EOFError:
        pass
    print(" Session saved. Continuing...\n")


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


def send_to_kindle(pdf_path: str) -> bool:
    env = load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    smtp_host = env.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(env.get("SMTP_PORT", "587"))
    smtp_user = env.get("SMTP_USER")
    smtp_pass = env.get("SMTP_PASS")
    kindle_email = env.get("KINDLE_EMAIL")

    missing = [k for k, v in {
        "SMTP_USER": smtp_user,
        "SMTP_PASS": smtp_pass,
        "KINDLE_EMAIL": kindle_email,
    }.items() if not v]
    if missing:
        print(f"\n[!] Cannot send to Kindle. Missing in .env: {', '.join(missing)}")
        print("    Add these to your .env:")
        print("      SMTP_HOST=smtp.gmail.com   (or your provider)")
        print("      SMTP_PORT=587")
        print("      SMTP_USER=tu_correo@gmail.com")
        print("      SMTP_PASS=app_password_de_16_caracteres")
        print("      KINDLE_EMAIL=tu_usuario@kindle.com")
        print("    Also add SMTP_USER to your Amazon 'Approved Personal Document E-mail List'.")
        return False

    msg = EmailMessage()
    msg["Subject"] = os.path.basename(pdf_path)
    msg["From"] = smtp_user
    msg["To"] = kindle_email
    msg.set_content("Enviado automáticamente desde web_to_kindle.py")

    with open(pdf_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(pdf_path),
        )

    print(f"[i] Sending {os.path.basename(pdf_path)} -> {kindle_email}")
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        print("[i] Email sent. Kindle delivery may take a few minutes.")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"[!] SMTP auth failed: {e}. For Gmail, use an App Password, not your normal password.")
        return False
    except Exception as e:
        print(f"[!] Failed to send email: {e}")
        return False


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    basename = build_output_basename(URL)
    pdf_path = os.path.join(script_dir, f"{basename}.pdf")

    first_run = not os.path.isdir(USER_DATA_DIR)
    if first_run:
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        print(f"[i] First run detected. Profile will be saved at: {USER_DATA_DIR}")
    else:
        print(f"[i] Reusing saved session from: {USER_DATA_DIR}")

    print(f"[1/4] Opening browser at {URL}")
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
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-default-browser-check",
                "--no-first-run",
                f"--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}",
            ],
            ignore_default_args=["--enable-automation"],
        )
        if USE_CHROME:
            try:
                context = p.chromium.launch_persistent_context(
                    channel="chrome", **launch_kwargs
                )
                print("[i] Using installed Google Chrome (with H.264/AAC codecs).")
            except Exception as e:
                print(f"[!] Chrome channel failed ({e}). Falling back to bundled Chromium.")
                context = p.chromium.launch_persistent_context(**launch_kwargs)
        else:
            context = p.chromium.launch_persistent_context(**launch_kwargs)
            print("[i] Using bundled Chromium (no proprietary codecs).")

        context.add_init_script(
            f"""
            Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});

            (function() {{
                const css = `
                    html {{ font-size: {int(TEXT_SCALE * 100)}% !important; }}
                    img, video, canvas, svg, picture, [style*="background-image"] {{
                        font-size: initial !important;
                    }}
                `;
                const inject = () => {{
                    if (document.getElementById('__kindle_style__')) return;
                    const s = document.createElement('style');
                    s.id = '__kindle_style__';
                    s.textContent = css;
                    (document.head || document.documentElement).appendChild(s);
                }};
                inject();
                new MutationObserver(inject).observe(document.documentElement, {{childList: true, subtree: true}});
            }})();
            """
        )

        page = context.pages[0] if context.pages else context.new_page()

        def kill_debugger_for_page(target_page):
            try:
                cdp = context.new_cdp_session(target_page)
                cdp.send("Debugger.enable")
                cdp.send("Debugger.setSkipAllPauses", {"skip": True})
            except Exception as ex:
                print(f"[!] Could not skip pauses on page: {ex}")

        kill_debugger_for_page(page)
        context.on("page", kill_debugger_for_page)
        print("[i] Anti 'debugger;' protections active.")

        page.goto(URL, wait_until="domcontentloaded")
        print(f"      Waiting {INITIAL_WAIT}s for initial content...")
        time.sleep(INITIAL_WAIT)

        if first_run or FORCE_LOGIN or needs_login(page):
            print(f"[i] Login required (current URL: {page.url})")
            print("[i] Opening a fresh tab for login (X flags the initial tab as automated).")
            login_page = context.new_page()
            try:
                login_page.goto(LOGIN_URL, wait_until="domcontentloaded")
            except Exception as e:
                print(f"[!] Could not open login URL ({e}). Log in manually in the new tab.")
            time.sleep(2)
            interactive_login(LOGIN_URL)
            try:
                login_page.close()
            except Exception:
                pass
            page.goto(URL, wait_until="domcontentloaded")
            time.sleep(INITIAL_WAIT)
            if needs_login(page):
                print("[!] Still not logged in after prompt. Aborting.")
                context.close()
                sys.exit(1)

        print(f"[2/4] Scrolling + capturing screenshots (max {MAX_SCROLLS} steps, "
              f"{VIEWPORT_HEIGHT}px each, {SCROLL_DELAY}s delay)")
        shots = scroll_and_capture(page)
        print(f"      Done. Captured {len(shots)} screenshots.")

        context.close()

    print(f"[3/4] Combining screenshots into PDF -> {pdf_path}")
    screenshots_to_pdf(shots, pdf_path)

    print(f"[4/4] Sending PDF to Kindle...")
    send_to_kindle(pdf_path)

    print("\nDone.")
    print(f"  PDF:  {pdf_path}")


if __name__ == "__main__":
    main()
