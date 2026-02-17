#!/usr/bin/env python3
"""Monitor TCF registration status and notify via ntfy.sh on changes."""

import os
import sys
import urllib.request
from html.parser import HTMLParser

URL = "https://www.afvictoria.ca/exams/tcf/"
STATE_FILE = os.environ.get("STATE_FILE", "last_state.txt")
NTFY_TOPIC = "tcf-registration-alert"

# Lines containing these substrings are filtered out
NOISE = [
    "please check regularly",
    "register for the",
    "if no dates are available",
]


class SessionParser(HTMLParser):
    """Extract session info from the section under the 'Next sessions' heading."""

    def __init__(self):
        super().__init__()
        self._in_section = False
        self._check_h2 = False
        self.lines = []
        self._current_line = ""

    def _flush(self):
        text = " ".join(self._current_line.split())  # normalize whitespace
        if text:
            self.lines.append(text)
        self._current_line = ""

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self._check_h2 = True
            self._current_line = ""
        elif self._in_section:
            if tag == "hr":
                self._flush()
                self.lines.append("---")
            elif tag in ("h1", "h2", "h3"):
                self._flush()
                self._in_section = False
            elif tag in ("p", "br"):
                self._flush()

    def handle_endtag(self, tag):
        if tag == "h2" and self._check_h2:
            self._check_h2 = False

    def handle_data(self, data):
        if self._check_h2:
            if "next sessions" in data.strip().lower():
                self._in_section = True
                self._check_h2 = False
            return
        if self._in_section:
            self._current_line += data

    def get_text(self):
        self._flush()
        result = []
        for line in self.lines:
            low = line.lower()
            if line == "---":
                result.append(line)
            elif not any(n in low for n in NOISE):
                result.append(line)
        # Strip trailing separator if present
        while result and result[-1] == "---":
            result.pop()
        return "\n".join(result)


def fetch_page():
    req = urllib.request.Request(URL, headers={"User-Agent": "TCF-Monitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def extract_status(html):
    parser = SessionParser()
    parser.feed(html)
    return parser.get_text()


def load_previous_state():
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def save_state(text):
    with open(STATE_FILE, "w") as f:
        f.write(text)


def notify(old, new):
    title = "TCF Registration Status Changed"
    body = f"New status:\n{new}"
    if old:
        body += f"\n\nPrevious status:\n{old}"

    data = body.encode("utf-8")
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=data,
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "rotating_light",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"Notification sent (HTTP {resp.status})")


def main():
    print(f"Fetching {URL} ...")
    html = fetch_page()

    current = extract_status(html)
    if not current:
        print("ERROR: Could not extract session info from page.", file=sys.stderr)
        sys.exit(1)

    print(f"Current status:\n{current}\n")

    previous = load_previous_state()

    if previous is None:
        print("No previous state found. Saving current state.")
        save_state(current)
    elif current != previous:
        print("STATUS CHANGED! Sending notification...")
        notify(previous, current)
        save_state(current)
    else:
        print("No change detected.")


if __name__ == "__main__":
    main()
