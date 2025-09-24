from dataclasses import dataclass
from typing import cast
from framework.plugin import BasePlugin, Tool
from framework.config import BaseConfig
import webbrowser
from tavily import TavilyClient, InvalidAPIKeyError


class WebBrowser(Tool):
    name = "web_browser"

    def invoke(self, url: str):
        """Open a specified website for the user. You can not get any information from it

        Args:
            url: the url of the website to open
        """
        webbrowser.open(url)
        return "success"


class WebSearch(Tool):
    name = "web_search"

    def invoke(self, query: str):
        """Search using the specified query to get rough infomation

        Args:
            query: the topic to search
        """
        try:
            response = cast(Plugin, self.plugin).tavily_client.search(query)
            return response["results"]
        except InvalidAPIKeyError:
            return "invalid tavily API key"


class WebExtract(Tool):
    name = "web_extract"

    def invoke(self, urls: list[str]):
        """Get detailed content of specified urls

        Args:
            urls: the urls to get content
        """
        try:
            response = cast(Plugin, self.plugin).tavily_client.extract(urls)
            return {k: response[k] for k in ("results", "failed_results")}
        except InvalidAPIKeyError:
            return "invalid tavily API key"


@dataclass
class Config(BaseConfig):
    tavily_api_key: str | None = None
    search: bool = True


class Plugin(BasePlugin):
    def init(self):
        api_key = cast(Config, self.get_config()).tavily_api_key
        if api_key is not None:
            self.tavily_client = TavilyClient(api_key)
        else:
            self.tavily_client = None

    def enable_search(self):
        return self.tavily_client is not None and cast(Config, self.get_config()).search

    def tools(self):
        l: list[Tool] = [WebBrowser]
        if self.enable_search():
            l.extend([WebSearch, WebExtract])
        return l
