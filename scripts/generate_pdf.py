"""Generate a print-ready PDF from the booklet page.

Fetches the booklet HTML from the local server and renders it to PDF
with intelligent page breaks using weasyprint.

Usage:
    python scripts/generate_pdf.py <booklet_url> [output.pdf]

Example:
    python scripts/generate_pdf.py "http://127.0.0.1:8000/booklet?category=db:4&lesson=abfddaf1-e837-4ed5-9d59-6e53056eb759" booklet.pdf
"""

import sys
import urllib.request

from weasyprint import HTML


# Additional CSS for print pagination
PRINT_CSS = """
@page {
    size: letter;
    margin: 1in 0.9in;

    @bottom-center {
        content: counter(page);
        font-family: 'Inter', sans-serif;
        font-size: 9pt;
        color: #999;
    }
}

/* Force page break before each major section */
.booklet-cover {
    page-break-after: always;
    padding-top: 3in;
    border-bottom: none;
}

.booklet-section {
    page-break-before: always;
    border-bottom: none;
    padding-bottom: 0;
}

/* The journey section itself doesn't need a break — the groups do */
.booklet-journey {
    page-break-before: always;
}
.booklet-journey > h2,
.booklet-journey > p {
    page-break-after: avoid;
}

/* Each journey group starts on a new page */
.journey-group {
    page-break-before: always;
    page-break-inside: avoid;
    box-shadow: none;
    border: 1px solid #ddd;
}

/* First group doesn't need a break (it follows the Journey header) */
.journey-group:first-of-type {
    page-break-before: avoid;
}

/* Keep voice boxes and insights together */
.what-was-happening {
    page-break-inside: avoid;
}
.voice-box {
    page-break-inside: avoid;
}

/* Keep headings with their content */
h2, h3, h4 {
    page-break-after: avoid;
}

/* Arc timeline should stay together */
.arc-timeline {
    page-break-inside: avoid;
}

/* Three states block */
.three-states {
    page-break-inside: avoid;
}

/* Override web background for print */
body {
    background: white;
}

/* Hide back link */
.back-link {
    display: none;
}
"""


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "booklet.pdf"

    print(f"Fetching {url} ...")
    with urllib.request.urlopen(url) as resp:
        html_content = resp.read().decode("utf-8")

    # Inject print CSS before </style>
    html_content = html_content.replace("</style>", PRINT_CSS + "\n</style>")

    print(f"Rendering PDF ...")
    HTML(string=html_content).write_pdf(output)

    print(f"Generated: {output}")


if __name__ == "__main__":
    main()
