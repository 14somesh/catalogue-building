import os
import queue
import threading
from typing import Dict, Any, Optional
from playwright.sync_api import sync_playwright

class TitleMeasurerThread(threading.Thread):
    """
    Dedicated worker thread hosting Playwright Headless Chromium on the actual
    A4 catalogue preview template (identical to src/4_build.py Rule 5).
    Using a dedicated thread prevents greenlet thread-switch conflicts with FastAPI threadpool.
    """
    _instance: Optional["TitleMeasurerThread"] = None
    _init_lock = threading.Lock()

    def __init__(self):
        super().__init__(daemon=True, name="TitleMeasurerWorker")
        self.req_q: queue.Queue = queue.Queue()
        self.ready_event = threading.Event()

    def run(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1200, "height": 1697},
                device_scale_factor=2
            )
            preview_path = os.path.abspath("dist/powerbank/combined/catalogue_preview.html").replace(os.sep, "/")
            if not os.path.exists("dist/powerbank/combined/catalogue_preview.html"):
                import glob
                htmls = glob.glob("dist/**/*.html", recursive=True)
                if htmls:
                    preview_path = os.path.abspath(htmls[0]).replace(os.sep, "/")

            page.goto(f"file:///{preview_path}", wait_until="networkidle")
            page.evaluate("() => document.fonts.ready")
            self.ready_event.set()

            while True:
                item = self.req_q.get()
                if item is None:
                    break
                title, res_q = item
                clean_title = str(title).strip()
                parts = clean_title.split()
                if len(parts) >= 2:
                    formatted = " ".join(parts[:-1]) + f" <em>{parts[-1]}</em>"
                else:
                    formatted = clean_title

                res = page.evaluate("""(formatted) => {
                    const el = document.querySelector('.prod__name');
                    if (!el) return { textWidth: 0, containerWidth: 291, overflow: false, diff: 0, percent: 0 };
                    el.innerHTML = '<span class="prod__num">01.</span> ' + formatted;
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    const textWidth = range.getBoundingClientRect().width;
                    const containerWidth = el.clientWidth || 291;
                    const diff = textWidth - containerWidth;
                    const overflow = textWidth > containerWidth + 1.0;
                    const percent = Math.round((textWidth / containerWidth) * 100);
                    return {
                        textWidth: Math.round(textWidth * 10) / 10,
                        containerWidth: Math.round(containerWidth * 10) / 10,
                        overflow: overflow,
                        diff: Math.round(diff * 10) / 10,
                        percent: percent
                    };
                }""", formatted)
                res_q.put(res)

            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    @classmethod
    def get_instance(cls) -> "TitleMeasurerThread":
        with cls._init_lock:
            if cls._instance is None:
                inst = TitleMeasurerThread()
                inst.start()
                cls._instance = inst
            return cls._instance

    def measure_title(self, title: str, timeout: float = 4.0) -> Dict[str, Any]:
        """Thread-safe measurement request."""
        self.ready_event.wait(timeout=10.0)
        res_q = queue.Queue()
        self.req_q.put((title, res_q))
        return res_q.get(timeout=timeout)

    def stop(self, timeout: float = 3.0):
        """Cleanly stops the worker thread and shuts down Chromium."""
        if not self.is_alive():
            return
        self.req_q.put((None, None))
        self.join(timeout=timeout)


def get_title_measurer() -> TitleMeasurerThread:
    return TitleMeasurerThread.get_instance()


def stop_title_measurer():
    """Shuts down the global title measurer and frees Chromium resources."""
    with TitleMeasurerThread._init_lock:
        if TitleMeasurerThread._instance is not None:
            TitleMeasurerThread._instance.stop()
            TitleMeasurerThread._instance = None


import atexit
atexit.register(stop_title_measurer)
