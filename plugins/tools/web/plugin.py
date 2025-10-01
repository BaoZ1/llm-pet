from dataclasses import dataclass
from typing import cast
from framework.plugin import BasePlugin, Tool
from framework.config import BaseConfig
from tavily import TavilyClient, InvalidAPIKeyError


class WebSearch(Tool):
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


class Plugin(BasePlugin):
    def init(self):
        self.tavily_client = TavilyClient(cast(Config, self.get_config()).tavily_api_key)

    def tools(self):
        return [WebSearch, WebExtract]
