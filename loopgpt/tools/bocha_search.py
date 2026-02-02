from loopgpt.tools.base_tool import BaseTool

import requests
import os


class BochaWebSearchTool(BaseTool):
    """This tool searches bocha for the given query and returns the results.

    Args:
        query (str): The query to search for.

    Returns:
        str: Search results.
    """

    def __init__(self):
        super(BochaWebSearchTool, self).__init__()
        self.api_key = os.environ.get("BOCHA_API_KEY")
        if not self.api_key:
            raise RuntimeError("BOCHA_API_KEY is not set")

    def run(self, query):
        url = "https://api.bochaai.com/v1/web-search"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "query": query,
            "summary": True,
            "count": 10
        }

        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()

        json_response = response.json()
        return json_response["data"]["webPages"]["value"]