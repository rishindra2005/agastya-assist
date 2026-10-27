#!/usr/bin/env python3
"""Live portal smoke script for Agastya Assist.

Opens each portal playbook's live URL (ITR, PAN, Voter, MCA) in headless Chromium,
takes a semantic snapshot, inspects whether the safety guard blocks each action,
and saves a screenshot to data/smoke/<id>.png.

Read-only: never fills or clicks buttons.
Exits non-zero if any page fails to load.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import socket
import sys
import urllib.parse

from agastya.browser.driver import BrowserDriver
from agastya.browser.guard import check_click
from agastya.portals import Playbook, load_playbooks

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Representative playbooks covering the 4 primary government portals:
# ITR (Income Tax), PAN (Protean/NSDL), Voter (ECI), MCA (Ministry of Corporate Affairs)
DEFAULT_PLAYBOOK_IDS = [
    "voter_form6",
    "pan_new_49a",
    "itr1_prefill",
    "mca_company_master",
]

# Fallbacks for live URLs with known DNS migration or SPA routing issues
URL_FALLBACKS: dict[str, list[str]] = {
    "www.onlineservices.nsdl.com": [
        "https://onlineservices.proteantech.in/paam/endUserRegisterContact.html",
    ],
    "eportal.incometax.gov.in": [
        "https://www.incometax.gov.in/iec/foportal/",
        "https://eportal.incometax.gov.in/iec/foservices/",
    ],
}


def _is_dns_resolvable(url: str) -> bool:
    """Check whether domain name in URL resolves via DNS."""
    try:
        host = urllib.parse.urlsplit(url).netloc
        if not host:
            return True
        socket.gethostbyname(host)
        return True
    except Exception:
        return False


def _get_candidate_urls(original_url: str) -> list[str]:
    """Get ordered list of candidate URLs including known fallbacks if primary domain migrated."""
    candidates = []
    # If original URL is resolvable, add it first
    if _is_dns_resolvable(original_url):
        candidates.append(original_url)
    else:
        logger.warning(f"Domain for {original_url} does not resolve via DNS.")

    # Check fallbacks
    for domain, fallbacks in URL_FALLBACKS.items():
        if domain in original_url:
            for fb in fallbacks:
                if fb not in candidates and _is_dns_resolvable(fb):
                    candidates.append(fb)

    # If nothing added yet, fall back to trying original anyway
    if not candidates:
        candidates.append(original_url)

    return candidates


async def smoke_playbook(
    playbook: Playbook,
    smoke_dir: Path,
) -> bool:
    """Visit live URL for a playbook, snapshot, evaluate guard, and save screenshot.

    Returns True if successfully loaded and screenshotted, False on failure.
    """
    logger.info(f"\n{'='*70}\n[SMOKE] Playbook: {playbook.id} | {playbook.title_en} ({playbook.portal})\n{'='*70}")

    candidate_urls = _get_candidate_urls(playbook.live_url)
    snapshot = None
    successful_url = ""
    last_error: Exception | None = None
    driver: BrowserDriver | None = None

    for url in candidate_urls:
        logger.info(f"Navigating to: {url} ...")
        driver = BrowserDriver(headless=True)
        try:
            await driver.start()
            snapshot = await asyncio.wait_for(driver.goto(url), timeout=25.0)
            successful_url = url
            break
        except Exception as exc:
            logger.warning(f"Failed to load {url}: {exc}")
            last_error = exc
            if driver is not None:
                await driver.close()
                driver = None

    if snapshot is None or driver is None:
        logger.error(f"FAILED: All candidate URLs for playbook '{playbook.id}' failed to load. Last error: {last_error}")
        return False

    try:
        # Print page info
        print(f"\nPlaybook ID  : {playbook.id}")
        print(f"Portal       : {playbook.portal}")
        print(f"Loaded URL   : {successful_url}")
        print(f"Page Title   : {snapshot.title}")
        print(f"Total Fields : {len(snapshot.fields)}")
        print(f"Total Actions: {len(snapshot.actions)}")

        # Print guard checks on all actions
        print("\nAction Safety Analysis:")
        if not snapshot.actions:
            print("  (No action buttons or links detected on initial snapshot)")
        for act in snapshot.actions:
            decision = check_click(act)
            status = "BLOCKED" if not decision.allowed else "ALLOWED"
            ref = act.get("ref", "")
            tag = act.get("tag", "")
            text = act.get("text", "").strip() or act.get("href", "")
            print(f"  [{status:7s}] {ref:4s} <{tag}>: {text[:50]} (reason: {decision.reason})")

        # Capture and save screenshot
        try:
            png_bytes = await driver.screenshot()
            out_path = smoke_dir / f"{playbook.id}.png"
            out_path.write_bytes(png_bytes)
            print(f"\nScreenshot saved: {out_path} ({len(png_bytes):,} bytes)")
        except Exception as exc:
            logger.warning(f"Could not capture screenshot for '{playbook.id}': {exc}")

        return True
    finally:
        await driver.close()


async def main() -> int:
    """Run live portal smoke suite."""
    smoke_dir = Path("data/smoke")
    smoke_dir.mkdir(parents=True, exist_ok=True)

    playbooks = load_playbooks()

    # Determine which playbooks to test
    if len(sys.argv) > 1 and sys.argv[1] != "--all":
        target_ids = sys.argv[1:]
    elif len(sys.argv) > 1 and sys.argv[1] == "--all":
        target_ids = list(playbooks.keys())
    else:
        target_ids = [pid for pid in DEFAULT_PLAYBOOK_IDS if pid in playbooks]

    logger.info(f"Starting live smoke run for {len(target_ids)} playbooks: {target_ids}")

    success_count = 0
    failure_count = 0

    for pid in target_ids:
        pb = playbooks.get(pid)
        if not pb:
            logger.error(f"Playbook ID not found: {pid}")
            failure_count += 1
            continue

        ok = await smoke_playbook(pb, smoke_dir)
        if ok:
            success_count += 1
        else:
            failure_count += 1

    print(f"\n{'='*70}\nLive Smoke Summary: {success_count} succeeded, {failure_count} failed\n{'='*70}")
    if failure_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
