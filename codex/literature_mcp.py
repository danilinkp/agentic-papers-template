"""Read-only stdio MCP for research metadata, the local Zotero library, and project PDFs."""

import base64
from html import unescape
import json
import re
import sys
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, Request, build_opener, urlopen
from xml.etree import ElementTree

from pdf_tools import pdf_info, pdf_read_pages, pdf_render_page

CROSSREF = "https://api.crossref.org/works"
ZOTERO = "http://127.0.0.1:23119/api/users/0/items"
ZOTERO_OPENER = build_opener(ProxyHandler({}))
ARXIV = "https://export.arxiv.org/api/query"
ATOM = {"atom": "http://www.w3.org/2005/Atom"}
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_RESPONSE = 2 * 1024 * 1024
LAST_ARXIV_REQUEST = 0.0


def fetch(url, accept, headers=None, open_request=urlopen):
    request = Request(url, headers={
        "Accept": accept, "User-Agent": "papers-template/0.1", **(headers or {}),
    })
    with open_request(request, timeout=15) as response:
        body = response.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        raise ValueError("Provider response is too large")
    return body


def normalize_crossref(work):
    issued = work.get("issued", {}).get("date-parts", [[None]])
    return {
        "provider": "Crossref",
        "title": next(iter(work.get("title", [])), None),
        "authors": [
            author.get("name") or " ".join(
                author.get(part, "") for part in ("given", "family")
            ).strip()
            for author in work.get("author", [])
        ],
        "year": issued[0][0] if issued and issued[0] else None,
        "venue": next(iter(work.get("container-title", [])), None),
        "type": work.get("type"),
        "doi": work.get("DOI"),
        "url": work.get("URL"),
    }


def crossref_search(query, limit=5):
    params = {
        "query.bibliographic": query,
        "rows": limit,
        "select": "DOI,title,author,issued,container-title,type,URL",
    }
    result = json.loads(fetch(f"{CROSSREF}?{urlencode(params)}", "application/json"))
    return [normalize_crossref(work) for work in result["message"]["items"]]


def crossref_lookup(doi):
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    if not re.fullmatch(r"10\.\d{4,}/[^\s]+", doi):
        raise ValueError("Expected a DOI such as 10.1093/comjnl/27.2.97")
    result = json.loads(fetch(f"{CROSSREF}/{quote(doi, safe='')}", "application/json"))
    work = result["message"]
    years = set()
    for field in ("issued", "published-online", "published-print"):
        for date in work.get(field, {}).get("date-parts", []):
            if date and isinstance(date[0], int):
                years.add(date[0])
    # Crossref update-to describes records this item updates, e.g. an erratum's target.
    return {
        **normalize_crossref(work),
        "first_author_family": next(
            (author.get("family") for author in work.get("author", [])), None
        ),
        "publication_years": sorted(years),
        "updates": work.get("update-to", []),
        "updated_by": work.get("updated-by", []),
    }


def arxiv_search(query, limit=5):
    global LAST_ARXIV_REQUEST

    wait = 3 - (time.monotonic() - LAST_ARXIV_REQUEST)
    if wait > 0:
        time.sleep(wait)
    params = {"search_query": query, "max_results": limit, "sortBy": "relevance"}
    try:
        body = fetch(f"{ARXIV}?{urlencode(params)}", "application/atom+xml")
    finally:
        LAST_ARXIV_REQUEST = time.monotonic()
    feed = ElementTree.fromstring(body)
    results = []
    for item in feed.findall("atom:entry", ATOM):
        url = item.findtext("atom:id", namespaces=ATOM)
        if not url or "/abs/" not in url:
            detail = item.findtext("atom:summary", default="Unknown error", namespaces=ATOM)
            raise ValueError(f"arXiv returned an error: {detail.strip()[:300]}")
        results.append({
            "provider": "arXiv",
            "type": "preprint",
            "arxiv_id": url.split("/abs/", 1)[1],
            "title": " ".join(item.findtext("atom:title", default="", namespaces=ATOM).split()),
            "authors": [node.text for node in item.findall("atom:author/atom:name", ATOM)],
            "published": item.findtext("atom:published", namespaces=ATOM),
            "doi": item.findtext("{http://arxiv.org/schemas/atom}doi"),
            "url": url.replace("http://", "https://", 1),
        })
    return results


def normalized_text(value):
    plain_text = re.sub(r"</?[A-Za-z][^>]*>", "", unescape(value))
    normalized = unicodedata.normalize("NFKC", plain_text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def verify_citation(doi, title, first_author_family=None, year=None):
    try:
        work = crossref_lookup(doi)
    except HTTPError as error:
        if error.code == 404:
            return {"status": "not_found_in_crossref", "doi": doi}
        raise
    expected = {"title": title}
    if first_author_family is not None:
        expected["first_author_family"] = first_author_family
    if year is not None:
        expected["year"] = year

    checks = {}
    for field, value in expected.items():
        if field == "year":
            actual = work["publication_years"]
            matches = value in actual
        else:
            actual = work[field]
            matches = bool(actual) and normalized_text(value) == normalized_text(actual)
        checks[field] = "match" if matches else "mismatch" if actual else "unavailable"

    status = "match"
    if "mismatch" in checks.values():
        status = "mismatch"
    elif "unavailable" in checks.values():
        status = "incomplete_metadata"
    return {"status": status, "checks": checks, "crossref": work}


def zotero_fetch(path, accept):
    return fetch(
        f"{ZOTERO}/{path}", accept, {"Zotero-API-Version": "3"}, ZOTERO_OPENER.open,
    )


def zotero_search(query, limit=5):
    query_string = urlencode({"q": query, "limit": limit})
    response = zotero_fetch(f"top?{query_string}", "application/json")
    fields = ("key", "itemType", "title", "creators", "date", "DOI", "url")
    return [
        {field: item["data"].get(field) for field in fields}
        for item in json.loads(response)
    ]


def zotero_export_bibtex(item_key):
    if not re.fullmatch(r"[A-Z0-9]{8}", item_key):
        raise ValueError("Expected the eight-character Zotero item key")
    response = zotero_fetch(f"{item_key}?format=bibtex", "*/*")
    return {"item_key": item_key, "bibtex": response.decode("utf-8")}


def string(description):
    return {"type": "string", "minLength": 1, "maxLength": 500, "description": description}


def integer(minimum, maximum):
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


LIMIT = {**integer(1, 15), "default": 5}
DOI = string("DOI or doi.org URL")
PDF = string("Path to a PDF file inside the project")
PAGE = integer(1, 100_000)
TOOLS = {
    "crossref_search": (
        crossref_search, "Search Crossref by title, keywords or author names.",
        {"query": string("Bibliographic search query"), "limit": LIMIT}, ["query"],
    ),
    "crossref_lookup": (
        crossref_lookup, "Resolve a DOI registered with Crossref and inspect its metadata.",
        {"doi": DOI}, ["doi"],
    ),
    "verify_citation": (
        verify_citation, "Compare a DOI with claimed citation metadata. Checks metadata, not quotations or scientific claims.",
        {"doi": DOI, "title": string("Cited title"), "first_author_family": string("First author's family name"),
         "year": integer(1000, 3000)}, ["doi", "title"],
    ),
    "arxiv_search": (
        arxiv_search, "Search preprints on arXiv, using queries such as ti:transformer AND cat:cs.CL.",
        {"query": string("arXiv API query"), "limit": LIMIT}, ["query"],
    ),
    "zotero_search": (
        zotero_search, "Search the personal library in the running Zotero Desktop application.",
        {"query": string("Title or author"), "limit": LIMIT}, ["query"],
    ),
    "zotero_export_bibtex": (
        zotero_export_bibtex, "Read the BibTeX export of a local Zotero item.",
        {"item_key": string("Zotero item key returned by zotero_search")}, ["item_key"],
    ),
    "pdf_info": (
        pdf_info, "Read the number of pages of a project PDF.", {"path": PDF}, ["path"],
    ),
    "pdf_read_pages": (
        pdf_read_pages, "Extract up to five PDF pages with layout. Page numbers use the physical PDF order.",
        {"path": PDF, "first_page": PAGE, "last_page": PAGE}, ["path", "first_page"],
    ),
    "pdf_render_page": (
        pdf_render_page, "Render one project PDF page as an image to inspect tables, equations, scans, or figures.",
        {"path": PDF, "page": PAGE}, ["path", "page"],
    ),
}


def tool_specs():
    for name, (_, description, properties, required) in TOOLS.items():
        yield {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": not name.startswith("pdf_")},
        }


def tool_result(payload, error=False):
    if isinstance(payload, bytes):
        content = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(payload).decode("ascii")}
    else:
        content = {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
    return {"content": [content], "isError": error}


def call_tool(params):
    name = params.get("name")
    if not isinstance(name, str) or name not in TOOLS:
        raise ValueError("Unknown tool")
    function, _, properties, required = TOOLS[name]
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    if arguments.keys() - properties.keys() or any(field not in arguments for field in required):
        raise ValueError("Missing or unexpected tool arguments")

    values = {}
    for field, value in arguments.items():
        schema = properties[field]
        if schema["type"] == "string":
            if not isinstance(value, str) or not value.strip() or len(value) > schema["maxLength"]:
                raise ValueError(f"Invalid text for {field}")
            value = value.strip()
        elif type(value) is not int or not schema["minimum"] <= value <= schema["maximum"]:
            raise ValueError(f"Invalid integer for {field}")
        values[field] = value
    try:
        return tool_result(function(**values))
    except (OSError, ValueError, KeyError, TypeError, ElementTree.ParseError) as error:
        message = str(error)
        if name.startswith("zotero_"):
            message += ". Start Zotero Desktop and enable its local API in advanced settings."
        return tool_result({"error": message}, error=True)


def rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request):
    if (
        not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
        or not isinstance(request.get("method"), str)
    ):
        return rpc_error(None, -32600, "Invalid request")
    if "id" not in request:
        return None  # MCP notifications do not receive responses.
    request_id = request["id"]
    method = request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return rpc_error(request_id, -32602, "Expected an object for params")

    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {
            "protocolVersion": requested if requested in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "academic-literature", "version": "0.1.0"},
            "instructions": "Verify bibliographic metadata and read project PDFs. Inspect primary sources before citing claims.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": list(tool_specs())}
    elif method == "tools/call":
        try:
            result = call_tool(params)
        except ValueError as error:
            return rpc_error(request_id, -32602, str(error))
    else:
        return rpc_error(request_id, -32601, "Unknown method")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main():
    for line in sys.stdin.buffer:
        try:
            response = handle(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = rpc_error(None, -32700, "Invalid JSON")
        if response is not None:
            print(json.dumps(response, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
