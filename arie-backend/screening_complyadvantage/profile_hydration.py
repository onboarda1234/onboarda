"""Phase G — on-demand ComplyAdvantage profile hydration (best-effort).

Reads the Mesh "Get risks within an alert" endpoint
(GET /v2/alerts/{alert_identifier}/risks) and extracts a LEAN set of display /
audit attributes from the matched profiles it returns inline. This module is
DISPLAY/AUDIT enrichment only: nothing here scores, gates, or triages. It is
pure-ish (deterministic given its inputs — no timestamps / Date.now) and
best-effort (any provider error while paging returns whatever was gathered so
far, never raises).

Every field path read below is documented so the extraction can be validated
and adjusted after the first live staging call — we have NO real risks-endpoint
response sample, so the reads follow the documented Mesh shape and are tolerant
of missing / renamed fields.

Documented risks response shape (per official Mesh docs)
--------------------------------------------------------
Top level:
  - risks[]           list of risk items (fallbacks tolerated: "values",
                      "data", "items")
  - page_number       current page (default 1)
  - page_size         page size (default 25 — we raise it)
  - next              opaque "more pages" signal (truthy → keep paging)
  - total_count       total risks across pages

Each risk item:
  - detail.profile    the matched profile (may also appear bare at
                      risk["profile"] — tolerated)
  - risk["sources"] / risk["references"]   watchlist / source metadata
                      (list name, listed date, related URLs) — read TOLERANTLY

Profile (detail.profile):
  - identifier / id                       → profile key for the hydration map
  - match_score / match_types / matching_name  (ranking metadata; not extracted
                      into attributes here — the stored hit already carries it)
  - person | company | vessel | aircraft  → the entity object

Person (profile.person):
  - names[]           {type, name|full_name|...}; type == "AKA" → alias
  - dates_of_birth[]  {source, value}      → date_of_birth (first value)
  - places_of_birth[] {source, value}      → places_of_birth
  - fields[]          {name, tag, value}   → nationality / countries / positions
                      mapped by tag (preferred) or name
  - images[]          {url|...}            → image_urls
  - associates / risk_indicators           (not surfaced here)

Company (profile.company): handled minimally — a company profile yields no
person attributes; the server already passes through company jurisdiction /
registration / aliases from the stored hit (F9r). We return an empty (or
watchlist-only) dict for company profiles so nothing person-shaped is invented.
"""

import logging

logger = logging.getLogger(__name__)


# ── tag/name matching for CA person ``fields`` ({name, tag, value}) ──
# Tag match is preferred; name match is the tolerant fallback. Substrings are
# matched case-insensitively so provider variations (e.g. "nationality" vs
# "Nationalities") still route to the right attribute.
_NATIONALITY_TOKENS = ("nationality", "nationalities")
_COUNTRY_TOKENS = ("country", "countries", "country of", "citizenship")
_POSITION_TOKENS = ("position", "occupation", "role", "title")


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _clean_text(value):
    """Collapse a scalar to a trimmed string; drop empties and dict/list."""
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list, tuple)):
        return ""
    return str(value).strip()


def _dedup_preserve_order(values):
    seen = set()
    out = []
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _profile_from(risk_or_profile):
    """Return (risk, profile). Accepts a full risk item ({detail:{profile}}),
    a bare-profile risk ({profile}), or a bare profile ({person}/{company})."""
    node = _as_dict(risk_or_profile)
    detail = _as_dict(node.get("detail"))
    if isinstance(detail.get("profile"), dict):
        return node, detail["profile"]
    if isinstance(node.get("profile"), dict):
        return node, node["profile"]
    # Treat the node itself as the profile (bare profile input).
    return node, node


def _name_text(name_entry):
    """Extract a display string from a CA name entry (dict or plain string)."""
    if isinstance(name_entry, str):
        return _clean_text(name_entry)
    entry = _as_dict(name_entry)
    direct = _clean_text(entry.get("name") or entry.get("full_name") or entry.get("value"))
    if direct:
        return direct
    parts = [
        _clean_text(entry.get("first_name") or entry.get("given_name")),
        _clean_text(entry.get("middle_name")),
        _clean_text(entry.get("last_name") or entry.get("family_name") or entry.get("surname")),
    ]
    return " ".join(part for part in parts if part).strip()


def _extract_aka_names(person):
    """person.names[] whose type contains "AKA" (alias/aka/also known)."""
    aka = []
    for entry in _as_list(person.get("names")):
        entry_dict = _as_dict(entry)
        type_text = _clean_text(entry_dict.get("type")).lower()
        if not any(token in type_text for token in ("aka", "alias", "also known")):
            continue
        text = _name_text(entry)
        if text:
            aka.append(text)
    return _dedup_preserve_order(aka)


def _extract_source_value_list(entries):
    """CA {source, value} collections → the value strings only."""
    values = []
    for entry in _as_list(entries):
        if isinstance(entry, str):
            values.append(entry)
            continue
        entry_dict = _as_dict(entry)
        values.append(entry_dict.get("value") or entry_dict.get("date") or entry_dict.get("name"))
    return _dedup_preserve_order(values)


def _extract_fields(person):
    """Map person.fields[] ({name, tag, value}) → nationality/countries/positions.

    Tag match is preferred, name match is the fallback. Returns a dict with only
    the non-empty attributes it could resolve.
    """
    nationality = ""
    countries = []
    positions = []
    for field in _as_list(person.get("fields")):
        field_dict = _as_dict(field)
        value = _clean_text(field_dict.get("value"))
        if not value:
            continue
        tag = _clean_text(field_dict.get("tag")).lower()
        name = _clean_text(field_dict.get("name")).lower()
        haystack = tag + " " + name
        if any(token in haystack for token in _NATIONALITY_TOKENS):
            if not nationality:
                nationality = value
            countries.append(value)
        elif any(token in haystack for token in _COUNTRY_TOKENS):
            countries.append(value)
        elif any(token in haystack for token in _POSITION_TOKENS):
            positions.append(value)
    out = {}
    if nationality:
        out["nationality"] = nationality
    countries = _dedup_preserve_order(countries)
    if countries:
        out["countries"] = countries
    positions = _dedup_preserve_order(positions)
    if positions:
        out["positions"] = positions
    return out


def _extract_image_urls(person):
    urls = []
    for image in _as_list(person.get("images")):
        if isinstance(image, str):
            urls.append(image)
            continue
        image_dict = _as_dict(image)
        urls.append(image_dict.get("url") or image_dict.get("source_url") or image_dict.get("href"))
    return _dedup_preserve_order(urls)


# ── watchlist / source metadata (read TOLERANTLY — no confirmed sample) ──
# Candidate containers checked, in order, on both the risk item and the profile.
_WATCHLIST_CONTAINER_KEYS = (
    "sources", "source", "watchlists", "watchlist", "listings", "listing",
    "references", "reference", "sanctions", "warnings", "risk_sources",
)
_WATCHLIST_NAME_KEYS = (
    "name", "list_name", "source_name", "list", "title", "authority",
    "publisher", "program",
)
_WATCHLIST_LISTED_KEYS = (
    "listed_date", "listed", "date", "from", "start_date", "published_at",
    "publication_date", "since",
)
_WATCHLIST_REMOVED_KEYS = (
    "removed_date", "removed", "to", "end_date", "delisted", "until",
)
_WATCHLIST_URL_KEYS = (
    "related_urls", "urls", "url", "source_url", "reference", "references",
    "link", "links",
)


def _collect_urls(container):
    urls = []
    for key in _WATCHLIST_URL_KEYS:
        raw = container.get(key)
        if isinstance(raw, str):
            urls.append(raw)
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, str):
                    urls.append(item)
                else:
                    item_dict = _as_dict(item)
                    urls.append(item_dict.get("url") or item_dict.get("href") or item_dict.get("value"))
        elif isinstance(raw, dict):
            urls.append(raw.get("url") or raw.get("href") or raw.get("value"))
    # Only keep http(s) links — never fabricate a link from a bare reference id.
    return _dedup_preserve_order(url for url in urls if _clean_text(url).lower().startswith(("http://", "https://")))


def _first_key(container, keys):
    for key in keys:
        value = _clean_text(container.get(key))
        if value:
            return value
    return ""


def _watchlist_entry_from(container):
    container = _as_dict(container)
    list_name = _first_key(container, _WATCHLIST_NAME_KEYS)
    listed_date = _first_key(container, _WATCHLIST_LISTED_KEYS)
    removed_date = _first_key(container, _WATCHLIST_REMOVED_KEYS)
    related_urls = _collect_urls(container)
    if not (list_name or listed_date or removed_date or related_urls):
        return None
    entry = {}
    if list_name:
        entry["list_name"] = list_name
    if listed_date:
        entry["listed_date"] = listed_date
    if removed_date:
        entry["removed_date"] = removed_date
    if related_urls:
        entry["related_urls"] = related_urls
    return entry


def _extract_watchlist_entries(risk, profile, person):
    """Scan candidate containers on the risk item, profile and person for
    watchlist/source metadata. Deduped by (list_name, listed_date)."""
    entries = []
    seen = set()

    def scan(node):
        node_dict = _as_dict(node)
        for key in _WATCHLIST_CONTAINER_KEYS:
            raw = node_dict.get(key)
            candidates = raw if isinstance(raw, (list, tuple)) else [raw]
            for candidate in candidates:
                entry = _watchlist_entry_from(candidate)
                if not entry:
                    continue
                dedup_key = (entry.get("list_name", "").lower(), entry.get("listed_date", "").lower())
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                entries.append(entry)

    scan(risk)
    scan(profile)
    scan(person)
    return entries


def extract_profile_attributes(risk_or_profile):
    """Return a LEAN dict of hydrated attributes from a risks[].detail.profile
    (or a bare profile / a full risk item). Only non-empty fields are emitted;
    nothing is invented. Person attributes are drawn from profile.person; a
    company profile yields only watchlist_entries (if any). Deterministic."""
    risk, profile = _profile_from(risk_or_profile)
    profile = _as_dict(profile)
    person = _as_dict(profile.get("person"))

    attributes = {}

    # Person attributes (only for person profiles).
    if person:
        dobs = _extract_source_value_list(person.get("dates_of_birth"))
        if dobs:
            attributes["date_of_birth"] = dobs[0]
        places = _extract_source_value_list(person.get("places_of_birth"))
        if places:
            attributes["places_of_birth"] = places
        attributes.update(_extract_fields(person))
        aka = _extract_aka_names(person)
        if aka:
            attributes["aka_names"] = aka
        images = _extract_image_urls(person)
        if images:
            attributes["image_urls"] = images

    # Watchlist / source metadata — applies to person AND company profiles.
    watchlist_entries = _extract_watchlist_entries(risk, profile, person)
    if watchlist_entries:
        attributes["watchlist_entries"] = watchlist_entries

    return attributes


def _profile_identifier(risk, profile):
    profile = _as_dict(profile)
    pid = _clean_text(profile.get("identifier") or profile.get("id"))
    if pid:
        return pid
    risk_dict = _as_dict(risk)
    return _clean_text(risk_dict.get("profile_identifier") or risk_dict.get("profile_id"))


def _risks_from_page(page):
    page = _as_dict(page)
    for key in ("risks", "values", "data", "items"):
        raw = page.get(key)
        if isinstance(raw, (list, tuple)):
            return list(raw)
    return []


def hydrate_alert_profiles(client, alert_identifier, *, wanted_profile_ids=None,
                           max_pages=4, page_size=100):
    """Page GET /v2/alerts/{alert_identifier}/risks and build
    {profile_identifier: attributes} for the requested profile ids (or every
    profile on the fetched pages when wanted_profile_ids is None).

    Best-effort: any exception / timeout / rate-limit while paging stops the
    loop and returns whatever was gathered so far — this never raises. Stops at
    max_pages or when the response carries no truthy ``next`` signal.
    Deterministic given identical provider responses.
    """
    wanted = None
    if wanted_profile_ids is not None:
        wanted = {_clean_text(pid) for pid in wanted_profile_ids if _clean_text(pid)}

    hydrated = {}
    if not _clean_text(alert_identifier):
        return hydrated

    page_number = 1
    pages_fetched = 0
    while pages_fetched < max(1, int(max_pages or 1)):
        try:
            page = client.get_alert_risks(
                alert_identifier, page_number=page_number, page_size=page_size,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort by contract
            logger.warning(
                "ca_profile_hydration_page_failed alert=%s page=%s exception=%s",
                alert_identifier, page_number, exc.__class__.__name__,
            )
            break
        pages_fetched += 1
        risks = _risks_from_page(page)
        for risk in risks:
            _, profile = _profile_from(risk)
            pid = _profile_identifier(risk, profile)
            if not pid:
                continue
            if wanted is not None and pid not in wanted:
                continue
            attributes = extract_profile_attributes(risk)
            if not attributes:
                continue
            if pid in hydrated:
                # Merge additively across pages without overwriting non-empty.
                for key, value in attributes.items():
                    hydrated[pid].setdefault(key, value)
            else:
                hydrated[pid] = attributes
        # Pagination: stop when no truthy "next" signal or no risks returned.
        if not _as_dict(page).get("next") or not risks:
            break
        page_number += 1

    return hydrated
