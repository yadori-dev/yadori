"""追加検索の上限と、少数の候補・原文の頁。"""

from __future__ import annotations

from dataclasses import dataclass

SEARCH_LIMIT = 3
CALL_LIMIT = 8
TEXT_LIMIT = 12000
PAGE_SIZE = 1000
QUERY_LIMIT = 120
EXCERPT_LIMIT = 120
CANDIDATE_LIMIT = 3
SEARCH_FLOOR = 0.75


class RecallFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code: str = code


@dataclass(frozen=True)
class Candidate:
    reference: str
    happened_at: str
    excerpt: str
    excerpt_truncated: bool
    already_suggested: bool


@dataclass(frozen=True)
class Candidates:
    status: str
    candidates: tuple[Candidate, ...]
    has_more: bool


@dataclass(frozen=True)
class Page:
    status: str
    reference: str
    offset: int
    text: str
    next_offset: int | None
    complete: bool


@dataclass(frozen=True)
class Problem:
    status: str
    message: str


@dataclass(frozen=True)
class Attempt:
    turn: str
    operation: str
    request: str
    response: str | None
