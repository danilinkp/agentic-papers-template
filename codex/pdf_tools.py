"""Extract and render pages of PDFs in the current research project."""

import os
from pathlib import Path
import re
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_PER_PAGE = 12_000


def project_pdf(path):
    candidate = (PROJECT_ROOT / path).resolve()
    if not candidate.is_relative_to(PROJECT_ROOT):
        raise ValueError("Use a PDF stored inside the project directory")
    if candidate.suffix.lower() != ".pdf" or not candidate.is_file():
        raise ValueError("Project PDF not found")
    return candidate


def poppler(command, *args):
    try:
        result = subprocess.run(
            [command, *args],
            capture_output=True,
            timeout=20,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
    except FileNotFoundError as error:
        raise ValueError(f"Install Poppler to use {command}") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"{command} did not finish in 20 seconds") from error
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip()[:500])
    return result.stdout


def page_count(pdf):
    metadata = poppler("pdfinfo", str(pdf)).decode(errors="replace")
    match = re.search(r"^Pages:\s+(\d+)$", metadata, re.MULTILINE)
    if not match:
        raise ValueError("Could not determine PDF page count")
    return int(match[1])


def pdf_info(path):
    pdf = project_pdf(path)
    return {"path": path, "page_count": page_count(pdf), "first_pdf_page": 1}


def pdf_read_pages(path, first_page, last_page=None):
    pdf = project_pdf(path)
    last_page = first_page if last_page is None else last_page
    if not first_page <= last_page <= first_page + 4:
        raise ValueError("Select at most five consecutive PDF pages in ascending order")
    if last_page > page_count(pdf):
        raise ValueError("The requested page is past the end of the PDF")
    output = poppler(
        "pdftotext", "-layout", "-enc", "UTF-8",
        "-f", str(first_page), "-l", str(last_page), str(pdf), "-",
    ).decode("utf-8", errors="replace")
    pages = []
    for offset, page_text in enumerate(output.split("\f")[:last_page - first_page + 1]):
        page_text = page_text.strip()
        pages.append({
            "pdf_page": first_page + offset,
            "text": page_text[:MAX_TEXT_PER_PAGE],
            "truncated": len(page_text) > MAX_TEXT_PER_PAGE,
            "has_text_layer_content": bool(page_text),
        })
    return {"path": path, "pages": pages}


def pdf_render_page(path, page):
    pdf = project_pdf(path)
    if page > page_count(pdf):
        raise ValueError("The requested page is past the end of the PDF")
    return poppler(
        "pdftoppm", "-png", "-singlefile", "-scale-to", "1700",
        "-f", str(page), "-l", str(page), str(pdf),
    )
