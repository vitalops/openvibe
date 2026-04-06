from __future__ import annotations

import asyncio
import atexit
import logging

from bs4 import BeautifulSoup
from pydantic import Field
from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait

from openvibe.summarizer import Summarizer
from openvibe.tool.base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_BOILERPLATE_TAGS = ["script", "style", "nav", "header", "footer", "aside"]


class WebBrowserTool(Tool):
    name = "web_browser"
    description = (
        "Open a URL in a headless browser. It opens the url and finds the "
        "answer for the query. Provide an empty query if you want to summarize "
        "the page. Returns a relevant summary and a list of links extracted "
        "from the URL."
    )

    class Params(Tool.Params):
        url: str = Field(description="URL to open.")
        query: str = Field(
            default="",
            description=(
                "Query to answer using the page content. "
                "Leave empty to get a general summary of the page."
            ),
        )
        browser_type: str = Field(
            default="chrome",
            description="Browser to use: 'chrome' (default) or 'firefox'.",
        )

    def __init__(self) -> None:
        self._driver: webdriver.Chrome | webdriver.Firefox | None = None
        self._browser_type: str = "chrome"
        self.summarizer = Summarizer()
        self.cache: dict[str, str] = {}
        self.browsed: dict[str, str] = {}
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # Driver lifecycle
    # ------------------------------------------------------------------

    def _set_browser_options(self, browser_type: str) -> None:
        if browser_type not in ("chrome", "firefox"):
            browser_type = "chrome"
        self._browser_type = browser_type
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/112.0.5615.49 Safari/537.36"
        )
        if self._browser_type == "chrome":
            from selenium.webdriver.chrome.options import Options

            options = Options()
            options.add_argument("--headless=new")
            options.add_argument(f"--user-agent={ua}")
            options.add_experimental_option("excludeSwitches", ["enable-logging"])
        else:
            from selenium.webdriver.firefox.options import Options

            options = Options()
            options.add_argument("--headless")
            options.add_argument(f"--user-agent={ua}")
        self.options = options

    def _init_chrome_driver(self) -> None:
        try:
            self._driver = webdriver.Chrome(options=self.options)
        except Exception as e:
            logger.info("Chrome driver failed (%s). Trying Firefox...", e)
            self._set_browser_options("firefox")
            self._init_driver()

    def _init_firefox_driver(self) -> None:
        self._driver = webdriver.Firefox(options=self.options)

    def _init_driver(self) -> None:
        self.close()
        if self._browser_type == "chrome":
            self._init_chrome_driver()
        else:
            self._init_firefox_driver()

    def close(self) -> None:
        try:
            if self._driver is not None:
                self._driver.quit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Page fetching
    # ------------------------------------------------------------------

    def _get(self, url: str) -> str:
        if url in self.cache:
            return self.cache[url]
        if self._driver is None:
            self._init_driver()
        num_retries = 3
        for _ in range(num_retries):
            try:
                self._driver.get(url)  # type: ignore[union-attr]
                break
            except NoSuchWindowException:
                self._init_driver()
                try:
                    self._driver.get(url)  # type: ignore[union-attr]
                    break
                except Exception:
                    continue
            except Exception:
                continue
        WebDriverWait(self._driver, 10).until(  # type: ignore[arg-type]
            expected_conditions.presence_of_element_located((By.TAG_NAME, "body"))
        )
        ret: str = self._driver.execute_script(  # type: ignore[union-attr]
            "return document.body.outerHTML;"
        )
        self.cache[url] = ret
        return ret

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _extract_links_from_soup(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        return [(link.text, link["href"]) for link in soup.find_all("a", href=True)]

    def _extract_text_from_soup(self, soup: BeautifulSoup) -> str:
        lines = (line.strip() for line in soup.get_text().splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        return "\n".join(chunk for chunk in chunks if chunk)

    # ------------------------------------------------------------------
    # execute
    # ------------------------------------------------------------------

    async def execute(
        self, ctx: ToolContext, params: "WebBrowserTool.Params"
    ) -> ToolResult:
        cache_key = f"{params.url}_{params.query}"
        if self.browsed.get(cache_key):
            return ToolResult(
                title=f"Browse {params.url}",
                output=self.browsed[cache_key],
                metadata={"truncated": True},
            )

        self._set_browser_options(params.browser_type)

        # Fetch and parse
        try:
            loop = asyncio.get_event_loop()
            html = await loop.run_in_executor(None, self._get, params.url)
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(_BOILERPLATE_TAGS):
                tag.extract()
            links = self._extract_links_from_soup(soup)[:5]
            text = self._extract_text_from_soup(soup)
        except Exception as e:
            return ToolResult(
                title=f"Browse {params.url}",
                output=(
                    f"An error occurred while scraping the website: {e}. "
                    "Make sure the URL is valid."
                ),
                error=True,
            )

        # Summarize
        try:
            summary, _chunks = await self.summarizer.summarize(text, params.query)
        except Exception as e:
            logger.warning("Summarizer failed for %s: %s", params.url, e)
            summary = text[:4000]

        result = (
            summary
            + "\n\n"
            + "Links found on the page:\n"
            + "\n".join([f"{link[1]}: {link[0]}" for link in links])
        )
        self.browsed[cache_key] = result
        return ToolResult(
            title=f"Browse {params.url}",
            output=result,
            metadata={"truncated": True},
        )
