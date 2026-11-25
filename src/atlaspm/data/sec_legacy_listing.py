"""Bounded legacy Item 5 evidence. Never produces certified identities."""
from __future__ import annotations

from datetime import datetime
import re

# Restrict broader patterns to the registrant's market section, not exhibits,
# investment holdings, or a generic symbol anywhere in the annual report.
ITEM5 = re.compile(r"\bITEM\s+5\s*[.:\-–]?\s*MARKET\s+FOR\b", re.I)
NEXT_ITEM = re.compile(r"\bITEM\s+(?:6|7|8|9|10)\s*[.:\-–]", re.I)
EXCHANGE = (r"New York and Pacific Stock Exchanges|New York Stock Exchange|"
            r"American Stock Exchange|Nasdaq National Market System|Nasdaq National Market|"
            r"Nasdaq SmallCap Market(?:\(SM\))?|Nasdaq Small Cap Index|"
            r"Nasdaq Stock Market|NYSE|Nasdaq")
TICKER = r"(?-i:[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*)"
QUOTED = rf"""["“'](?P<symbol>{TICKER})\.?["”']"""
BARE = rf"(?P<symbol>{TICKER})(?=[.,;]|\s|$)"
STOCK = r"(?:(?:class [A-Z0-9]+ )?(?:common stock|common shares)|class [A-Z0-9]+ stock)"
VERB = r"(?:trades|is (?:listed|traded))"
VENUE = rf"(?:on|in) (?:the )?(?P<exchange>{EXCHANGE})"
OWNER = r"(?:our|the company['’]s|the registrant['’]s|the)"


def _section_ranges(text):
    for start in ITEM5.finditer(text):
        end = NEXT_ITEM.search(text, start.end())
        stop = min(end.start() if end else len(text), start.end() + 30000)
        # A table of contents entry is followed by the next item and
        # normally contains no listing sentence. Keep offsets in full text.
        yield start.end(), stop


def _owner(issuer):
    # Only an exact first issuer-name token, not arbitrary named entities.
    # This is a lead even when the token is shared by multiple issuers.
    name = re.match(r"[A-Z][A-Z0-9]{2,}", issuer or "", re.I)
    return rf"(?:{OWNER}|{re.escape(name.group())}['’]s)" if name else OWNER


def listing_matches(text, issuer):
    owner = _owner(issuer)
    # Unquoted bare symbols require either an explicit named share class
    # or the comma/parenthetical ticker syntax observed in real filings.
    patterns = (
        rf"\b{owner} {STOCK} {VERB} {VENUE} under (?:the )?(?:trading |ticker )?symbol\s*[: ]*\s*{QUOTED}",
        rf"\b{owner} class [A-Z0-9]+ (?:common )?stock {VERB} {VENUE} under the symbol {BARE}",
        rf"\b{owner} {STOCK} {VERB} {VENUE},\s*(?:ticker|trading) symbol {BARE}",
        rf"\b{owner} {STOCK}\s*\(symbol {BARE}\)\s*{VERB} {VENUE}",
        rf"\b{owner} {STOCK},\s*(?:ticker|trading) symbol {QUOTED},\s*{VERB} {VENUE}",
    )
    # Parenthesis must be allowed as a boundary for the parenthetical case.
    patterns = tuple(p.replace(r"(?=[.,;]|\s|$)", r"(?=[.,;)]|\s|$)") for p in patterns)
    compiled = [re.compile(p, re.I) for p in patterns]
    for start, end in _section_ranges(text):
        for pattern in compiled:
            for match in pattern.finditer(text, start, end):
                prefix = text[max(start, match.start()-100):match.start()]
                # Never take the current-symbol matcher from a historical,
                # hypothetical or explicitly third-party clause.
                if re.search(r"\b(?:prior to|formerly|previously|if)\b[^.!?;]{0,85}$", prefix, re.I):
                    continue
                yield match


_CHANGE = re.compile(
    rf"""\bthe company['’]s stock is traded {VENUE}\.\s*Formerly, the symbol was ["“'](?P<old>{TICKER})["”']\.\s*"""
    rf"""The symbol changed to ["“'](?P<new>{TICKER})["”'] effective on (?P<date>[A-Z][a-z]+ \d{{1,2}}, \d{{4}})\.""",
    re.I,
)
_TRANSFER = re.compile(
    rf"""\bour common stock trades on (?:the )?(?P<new_exchange>{EXCHANGE}) under the symbol ["“'](?P<new>{TICKER})["”']\.\s*"""
    rf"""Prior to (?P<date>[A-Z][a-z]+ \d{{1,2}}, \d{{4}}), our common stock was traded on (?:the )?(?P<old_exchange>{EXCHANGE}) under the symbol ["“'](?P<old>{TICKER})["”']""",
    re.I,
)


def change_leads(text, filed_date):
    seen = set()
    results = []
    for start, end in _section_ranges(text):
        for pattern, kind in ((_CHANGE, "SYMBOL_CHANGE_TEXT"), (_TRANSFER, "VENUE_TRANSFER_TEXT")):
            for match in pattern.finditer(text, start, end):
                try:
                    day = datetime.strptime(match["date"], "%B %d, %Y").date().isoformat()
                except ValueError:
                    continue
                if day > filed_date or match["old"] == match["new"]:
                    continue
                key = (kind, match["old"], match["new"], day)
                if key in seen:
                    continue
                seen.add(key)
                groups = match.groupdict()
                results.append({
                    "kind": kind, "old_symbol_candidate": match["old"],
                    "new_symbol_candidate": match["new"],
                    "reported_change_date": day, "effective_date": None,
                    "old_exchange_reported": groups.get("old_exchange"),
                    "new_exchange_reported": groups.get("new_exchange", groups.get("exchange")),
                    "matched_text": match.group(0), "text_offset": match.start(),
                    "known_date_lower_bound": filed_date, "security_id": None,
                    "valid_from": None, "valid_to": None,
                    "status": "UNVERIFIED_LEGACY_CHANGE_TEXT",
                })
                if len(results) == 20:
                    return results
    return results
