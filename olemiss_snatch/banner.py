"""Client for Ole Miss's public Banner 9 class search (no login required)."""

import html
import time
from dataclasses import dataclass

import requests

BASE_URL = "https://reg-prod.olemiss.elluciancloud.com/StudentRegistrationSsb/ssb"
USER_AGENT = "olemiss-snatch (seat-opening alerts; github.com/codyrchen/olemiss-snatch)"
PAGE_SIZE = 500


@dataclass(frozen=True)
class Section:
    term: str
    crn: str
    subject: str
    course_number: str
    section: str
    title: str
    seats_available: int
    max_enrollment: int
    wait_available: int

    @property
    def is_full(self) -> bool:
        return self.max_enrollment > 0 and self.seats_available <= 0

    @property
    def is_closed(self) -> bool:
        """Capacity 0 usually means cancelled or closed to normal registration."""
        return self.max_enrollment <= 0

    @property
    def label(self) -> str:
        return f"{self.subject} {self.course_number}-{self.section}"


def parse_section(raw: dict) -> Section:
    return Section(
        term=str(raw["term"]),
        crn=str(raw["courseReferenceNumber"]),
        subject=raw["subject"],
        course_number=raw["courseNumber"],
        section=raw["sequenceNumber"],
        title=html.unescape(raw.get("courseTitle") or ""),
        seats_available=int(raw.get("seatsAvailable") or 0),
        max_enrollment=int(raw.get("maximumEnrollment") or 0),
        wait_available=int(raw.get("waitAvailable") or 0),
    )


class BannerClient:
    def __init__(self, base_url: str = BASE_URL, delay: float = 1.0):
        self.base_url = base_url
        self.delay = delay  # seconds between requests, to be polite to Banner
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._term = None
        self._session_id = None

    def _get(self, path: str, **params):
        time.sleep(self.delay)
        r = self.session.get(f"{self.base_url}/{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, params=None, data=None):
        time.sleep(self.delay)
        r = self.session.post(f"{self.base_url}/{path}", params=params, data=data, timeout=30)
        r.raise_for_status()
        return r

    def get_terms(self, max_results: int = 20) -> list[dict]:
        return self._get("classSearch/getTerms", searchTerm="", offset=1, max=max_results)

    def get_subjects(self, term: str) -> list[dict]:
        return self._get("classSearch/get_subject", searchTerm="", term=term, offset=1, max=1000)

    def _select_term(self, term: str):
        """Banner search is stateful: the session cookie remembers the chosen term."""
        if self._term == term:
            return
        self._session_id = f"snatch{int(time.time() * 1000)}"
        self._post("term/search", params={"mode": "search"}, data={
            "term": term, "studyPath": "", "studyPathText": "",
            "startDatepicker": "", "endDatepicker": "",
            "uniqueSessionId": self._session_id,
        })
        self._term = term

    def search_subject(self, term: str, subject: str) -> list[Section]:
        self._select_term(term)
        self._post("classSearch/resetDataForm")
        sections, offset = [], 0
        while True:
            r = self._get(
                "searchResults/searchResults",
                txt_subject=subject, txt_term=term,
                pageOffset=offset, pageMaxSize=PAGE_SIZE,
                sortColumn="subjectDescription", sortDirection="asc",
                uniqueSessionId=self._session_id,
            )
            page = r.get("data") or []
            sections += [parse_section(s) for s in page]
            offset += len(page)
            if not page or offset >= (r.get("totalCount") or 0):
                return sections
