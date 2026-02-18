#!/usr/bin/env python3
"""Monitor TCF and TEF registration status and notify via ntfy.sh on changes."""

import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

PAGES = {
    "TCF": "https://www.afvictoria.ca/exams/tcf/",
    "TEF": "https://www.afvictoria.ca/exams/tef/",
}
NTFY_TOPIC = "tcf-registration-alert"

# Gist-based state storage (set env vars for Render; falls back to local file)
GIST_ID = os.environ.get("GIST_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
STATE_FILE = os.environ.get("STATE_FILE", "last_state.json")

# Lines containing these substrings (lowercased) are filtered out
NOISE = [
    "please check regularly",
    "register for the",
    "register for tef",
    "if no dates are available",
]


class SessionParser(HTMLParser):
    """Extract all session/status/registration lines from the page.

    Captures any line containing keywords like "session:", "status:",
    "registration", "next session:", or "sold out". Also preserves
    the "Next sessions" heading and <hr> separators for structure.
    """

    def __init__(self):
        super().__init__()
        self.lines = []
        self._current_line = ""
        self._in_strong = False

    def _flush(self):
        text = " ".join(self._current_line.split())
        if text:
            self.lines.append(text)
        self._current_line = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br", "h1", "h2", "h3", "tr", "td"):
            self._flush()
        elif tag == "hr":
            self._flush()
            self.lines.append("---")
        elif tag == "strong":
            self._in_strong = True

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3"):
            self._flush()
        elif tag == "strong":
            self._in_strong = False

    def handle_data(self, data):
        self._current_line += data

    def get_text(self):
        self._flush()
        # Match lines containing session/status info
        pattern = re.compile(
            r"(next session\s*:|session\s*:|status\s*:|registration starts)",
            re.IGNORECASE,
        )
        result = []
        for line in self.lines:
            low = line.lower()
            if line == "---":
                result.append(line)
            elif pattern.search(low):
                if low == "next sessions":
                    result.append("---")
                elif not any(n in low for n in NOISE):
                    result.append(line)

        # Clean up: remove leading/trailing/consecutive separators
        cleaned = []
        for line in result:
            if line == "---" and (not cleaned or cleaned[-1] == "---"):
                continue
            cleaned.append(line)
        while cleaned and cleaned[-1] == "---":
            cleaned.pop()

        return "\n".join(cleaned)


def fetch_page(url):
    req = urllib.request.Request(url, headers={"User-Agent": "TCF-Monitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def extract_status(html):
    parser = SessionParser()
    parser.feed(html)
    return parser.get_text()


# --- State persistence (Gist or local file) ---

def load_previous_state():
    if GIST_ID and GITHUB_TOKEN:
        return _load_state_gist()
    return _load_state_file()


def save_state(state):
    if GIST_ID and GITHUB_TOKEN:
        _save_state_gist(state)
    else:
        _save_state_file(state)


def _load_state_file():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state_file(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _load_state_gist():
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}",
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            gist = json.loads(resp.read().decode())
            content = gist["files"]["last_state.json"]["content"]
            return json.loads(content)
    except (KeyError, json.JSONDecodeError, urllib.error.HTTPError) as e:
        print(f"  Could not load gist state: {e}")
        return {}


def _save_state_gist(state):
    payload = json.dumps({
        "files": {
            "last_state.json": {
                "content": json.dumps(state, indent=2)
            }
        }
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}",
        data=payload,
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"  State saved to gist (HTTP {resp.status})")


# --- Notification ---

def notify(name, old, new):
    title = f"{name} Registration Status Changed"
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
        print(f"  Notification sent (HTTP {resp.status})")


def main():
    previous_state = load_previous_state()
    current_state = {}
    any_change = False

    for name, url in PAGES.items():
        print(f"[{name}] Fetching {url} ...")
        html = fetch_page(url)
        current = extract_status(html)

        if not current:
            print(f"  ERROR: Could not extract session info.", file=sys.stderr)
            sys.exit(1)

        current_state[name] = current
        previous = previous_state.get(name)

        print(f"  Current status:\n{current}\n")

        if previous is None:
            print(f"  No previous state. Saving baseline.")
        elif current != previous:
            print(f"  STATUS CHANGED! Sending notification...")
            notify(name, previous, current)
            any_change = True
        else:
            print(f"  No change.")

    save_state(current_state)

    if not any_change and previous_state:
        print("\nNo changes detected on any page.")


if __name__ == "__main__":
    main()
