"""Daily provider check. Run from cPanel cron, for example:

    0 3 * * * cd ~/tawasol/backend && ~/virtualenv/tawasol/3.11/bin/python -m jobs.daily_check >> ~/logs/daily_check.log 2>&1

Planned steps (see docs/doctor-directory-plan.md):
1. Load sources and the providers due for a check.
2. Fetch each source page politely (rate-limited, respecting terms).
3. Normalise text (Unicode NFKC) and write one row per field to `observations`.
4. Compare with the current values; write differences to `change_events`
   with review_status = 'pending' for a human to approve.
5. Mark providers not seen for a long time as `possibly_stale`.
"""
import logging
import unicodedata

log = logging.getLogger("daily_check")


def normalise(text: str) -> str:
    """NFKC fixes Arabic presentation forms copied from PDFs (e.g. ﻧﻬﺎل -> نهال)."""
    return unicodedata.normalize("NFKC", text).strip()


def run() -> None:
    log.info("daily check started")
    # TODO: implement the steps in the module docstring.
    log.info("daily check finished")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
