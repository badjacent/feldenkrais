#!/usr/bin/env python3
"""
One-off script to inspect the FMTT course page structure.
Uses the same auth as fm_loader.py to see what date info is available.
"""

import os
import sys
from pathlib import Path

# Add src/kb to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from fm_loader import create_session, BASE_URL, COURSE_URL
from bs4 import BeautifulSoup


def main():
    email = os.environ.get("FM_EMAIL")
    password = os.environ.get("FM_PASSWORD")

    if not email or not password:
        print("Set FM_EMAIL and FM_PASSWORD environment variables")
        sys.exit(1)

    print(f"Logging in as {email}...")
    session = create_session(email, password)

    print(f"Fetching course page: {COURSE_URL}")
    response = session.get(COURSE_URL)

    soup = BeautifulSoup(response.text, 'html.parser')

    # Save full HTML for inspection
    output_path = Path(__file__).parent / "fmtt_course_page.html"
    with open(output_path, 'w') as f:
        f.write(response.text)
    print(f"\nSaved full HTML to: {output_path}")

    # Look for lesson links and surrounding context
    print("\n" + "=" * 60)
    print("LESSON LINKS AND SURROUNDING CONTENT")
    print("=" * 60)

    # Find all links to lessons
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        if '/lessons/' in href and BASE_URL in href:
            # Get parent elements to see context (dates, etc.)
            text = link.get_text(strip=True)
            if text and text != 'Start':
                print(f"\n--- Lesson: {text} ---")
                print(f"URL: {href}")

                # Look at parent structure
                parent = link.parent
                for i in range(5):  # Go up 5 levels
                    if parent:
                        parent_text = parent.get_text(separator=' | ', strip=True)[:200]
                        print(f"  Parent {i+1} ({parent.name}): {parent_text}...")
                        parent = parent.parent

    # Also look for any date patterns in the page
    print("\n" + "=" * 60)
    print("LOOKING FOR DATE PATTERNS")
    print("=" * 60)

    import re
    date_patterns = [
        r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',  # MM/DD/YYYY
        r'\b\d{4}-\d{2}-\d{2}\b',  # YYYY-MM-DD
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b',  # Month DD, YYYY
        r'\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}\b',  # DD Month YYYY
    ]

    text = soup.get_text()
    for pattern in date_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            print(f"Pattern '{pattern}': {matches[:10]}...")  # First 10 matches


if __name__ == "__main__":
    main()
