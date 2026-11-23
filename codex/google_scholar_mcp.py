"""Run the pinned upstream Scholar server with HTTP errors kept off MCP stdout."""

from functools import partial
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import requests


def get_scholar_page(url, **kwargs):
    response = requests.get(url, timeout=15, **kwargs)
    response.raise_for_status()
    return response


def main():
    upstream = Path(__file__).resolve().parents[1] / ".local/google-scholar-mcp"
    sys.path.insert(0, str(upstream))

    import google_scholar_web_search

    google_scholar_web_search.print = partial(print, file=sys.stderr)
    google_scholar_web_search.requests = SimpleNamespace(get=get_scholar_page)
    runpy.run_path(str(upstream / "google_scholar_server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
