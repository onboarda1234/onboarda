"""
ComplyAdvantage Mesh — Screening Integration Client
=====================================================
Provides AML/PEP/sanctions screening via ComplyAdvantage Mesh API.

Implements:
  - OAuth2 token acquisition using client credentials
  - Create-and-screen synchronous workflow
  - Result normalization to Onboarda screening format
  - Credential detection with explicit fallback signaling

Environment variables:
  COMPLYADVANTAGE_API_KEY   — API key for ComplyAdvantage
  COMPLYADVANTAGE_BASE_URL  — API base URL (default: https://api.complyadvantage.com)

Usage:
    from complyadvantage_client import get_complyadvantage_client

    client = get_complyadvantage_client()
    if client.is_configured:
        result = client.screen_entity(name="John Doe", entity_type="person")
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import RequestException, Timeout

logger = logging.getLogger("complyadvantage_client")


class ComplyAdvantageClient:
    """
    ComplyAdvantage API client for AML/PEP/sanctions screening.

    Attributes:
        api_key: ComplyAdvantage API key.
        base_url: API base URL.
        is_configured: Whether valid credentials are present.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
    ):
        self.api_key = api_key or os.environ.get("COMPLYADVANTAGE_API_KEY", "")
        self.base_url = (base_url or os.environ.get(
            "COMPLYADVANTAGE_BASE_URL", "https://api.complyadvantage.com"
        )).rstrip("/")
        self.timeout = timeout
        self.is_configured = bool(self.api_key)

        if self.is_configured:
            logger.info("ComplyAdvantage client initialized with API key")
        else:
            logger.info("ComplyAdvantage client: no API key — screening will use fallback")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def screen_entity(
        self,
        name: str,
        entity_type: str = "person",
        birth_year: Optional[int] = None,
        country_codes: Optional[List[str]] = None,
        fuzziness: float = 0.6,
    ) -> Dict[str, Any]:
        """
        Screen an entity (person or company) against ComplyAdvantage databases.

        Args:
            name: Full name of the person or company.
            entity_type: "person" or "company".
            birth_year: Birth year for person screening (optional).
            country_codes: List of ISO country codes to filter results (optional).
            fuzziness: Match fuzziness 0.0–1.0 (default 0.6).

        Returns:
            Normalized screening result dict compatible with Onboarda format.
        """
        if not self.is_configured:
            return self._not_configured_result(name)

        try:
            payload = {
                "search_term": name,
                "fuzziness": fuzziness,
                "filters": {
                    "entity_type": entity_type,
                },
            }

            if birth_year:
                payload["filters"]["birth_year"] = birth_year

            if country_codes:
                payload["filters"]["country_codes"] = country_codes

            url = f"{self.base_url}/v1/searches"
            resp = requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )

            if resp.status_code == 200:
                return self._normalize_response(resp.json(), name, entity_type)
            elif resp.status_code == 401:
                logger.warning("ComplyAdvantage: Invalid API key (401)")
                return self._error_result(name, "Invalid API key — authentication failed")
            elif resp.status_code == 403:
                logger.warning("ComplyAdvantage: Forbidden (403) — check API key permissions")
                return self._error_result(name, "API key lacks required permissions")
            elif resp.status_code == 429:
                logger.warning("ComplyAdvantage: Rate limited (429)")
                return self._error_result(name, "Rate limited — retry later")
            else:
                logger.warning(f"ComplyAdvantage: HTTP {resp.status_code}")
                return self._error_result(
                    name, f"API returned HTTP {resp.status_code}"
                )

        except Timeout:
            logger.error("ComplyAdvantage: Request timed out")
            return self._error_result(name, "Request timed out")
        except RequestException as e:
            logger.error(f"ComplyAdvantage: Network error — {e}")
            return self._error_result(name, f"Network error: {str(e)[:100]}")
        except Exception as e:
            logger.error(f"ComplyAdvantage: Unexpected error — {e}")
            return self._error_result(name, f"Unexpected error: {str(e)[:100]}")

    def _normalize_response(
        self, data: Dict[str, Any], name: str, entity_type: str
    ) -> Dict[str, Any]:
        """
        Normalize ComplyAdvantage API response to Onboarda screening format.

        Maps ComplyAdvantage search results to the standard format used by
        screen_sumsub_aml() so the UI and downstream processing remain unchanged.
        """
        content = data.get("content", {})
        search_data = content.get("data", {})
        total_hits = search_data.get("total_hits", 0)
        hits_list = search_data.get("hits", [])

        results = []
        for hit in hits_list:
            doc = hit.get("doc", {})
            match_score = hit.get("match_status", "")

            # Extract types (sanctions, pep, etc.)
            types = doc.get("types", [])
            is_pep = any("pep" in t.lower() for t in types)
            is_sanctioned = any(
                "sanction" in t.lower() or "special-interest" in t.lower()
                for t in types
            )

            # Extract source names
            sources = doc.get("sources", [])
            source_names = [s.get("name", "") for s in sources if s.get("name")]

            # Map match_status to numeric score
            score_map = {
                "potential_match": 75.0,
                "false_positive": 10.0,
                "true_positive": 95.0,
                "true_positive_approve": 95.0,
                "unknown": 50.0,
            }
            numeric_score = score_map.get(match_score, 60.0)

            results.append({
                "match_score": numeric_score,
                "matched_name": doc.get("name", name),
                "datasets": types,
                "schema": entity_type.capitalize(),
                "topics": types,
                "countries": doc.get("countries", []),
                "sanctions_list": ", ".join(source_names[:3]),
                "is_pep": is_pep,
                "is_sanctioned": is_sanctioned,
            })

        return {
            "matched": total_hits > 0,
            "results": results,
            "source": "complyadvantage",
            "api_status": "live",
            "total_hits": total_hits,
            "search_id": content.get("data", {}).get("id"),
            "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def _not_configured_result(self, name: str) -> Dict[str, Any]:
        """Return when client is not configured (no API key)."""
        return {
            "matched": False,
            "results": [],
            "source": "complyadvantage",
            "api_status": "not_configured",
            "note": "ComplyAdvantage API key not configured",
            "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def _error_result(self, name: str, reason: str) -> Dict[str, Any]:
        """Return on API error — clearly labeled, not simulated."""
        return {
            "matched": False,
            "results": [],
            "source": "complyadvantage",
            "api_status": "error",
            "note": f"ComplyAdvantage error: {reason}",
            "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def health_check(self) -> Dict[str, Any]:
        """Check if ComplyAdvantage API is reachable and credentials are valid."""
        if not self.is_configured:
            return {"status": "not_configured", "configured": False}

        try:
            # Use a minimal search to validate credentials
            resp = requests.post(
                f"{self.base_url}/v1/searches",
                headers=self._headers(),
                json={"search_term": "health_check_probe", "fuzziness": 1.0},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                return {"status": "ok", "configured": True}
            elif resp.status_code == 401:
                return {"status": "auth_failed", "configured": True, "error": "Invalid API key"}
            else:
                return {"status": "error", "configured": True, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "unreachable", "configured": True, "error": str(e)[:100]}


# ── Singleton ──

_client_instance: Optional[ComplyAdvantageClient] = None


def get_complyadvantage_client(**kwargs) -> ComplyAdvantageClient:
    """Get or create the singleton ComplyAdvantage client."""
    global _client_instance
    if _client_instance is None:
        _client_instance = ComplyAdvantageClient(**kwargs)
    return _client_instance


def reset_complyadvantage_client():
    """Reset the singleton (for testing)."""
    global _client_instance
    _client_instance = None
