"""Privacy and SSRF guards for knowledge acquisition.

No function in this module resolves DNS or opens a socket.  It validates
de-identified acquisition inputs and the evidence a future transport must
return after every redirect.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from continucare.knowledge.ops.models import KnowledgeOpsPolicyError, SourcePolicy


_FORBIDDEN_DATA_KEYS = frozenset(
    {
        "patient_id",
        "patient_identifier",
        "patient_name",
        "subject_id",
        "medical_record_number",
        "mrn",
        "encounter_id",
        "date_of_birth",
        "dob",
        "phone",
        "phone_number",
        "email",
        "email_address",
        "street_address",
        "home_address",
        "national_id",
        "id_card_number",
    }
)
_SENSITIVE_QUERY_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "signature",
    "credential",
    "session",
    "cookie",
)
_TECHNICAL_VALUE_KEYS = frozenset(
    {
        "code",
        "version",
        "document_version",
        "canonical_url",
        "relative_path",
        "content_type",
        "collection",
        "policy_id",
        "profile_id",
        "validation_profile_id",
    }
)
AUDITED_SHA256_FIELD_EVIDENCE = MappingProxyType(
    {
        # AppendOnlyLedger creates and replays these over canonical entry bytes.
        "entry_sha256": "AppendOnlyLedger.append/_history_unlocked canonical entry digest",
        "supersedes_entry_sha256": "AppendOnlyLedger predecessor-chain replay",
        # Acquisition/connectors create these from exact bytes or canonical metadata.
        "catalog_sha256": "OfflineFixtureConnector recomputes fixture catalog bytes",
        "content_sha256": "connector/quarantine recompute exact content bytes",
        "metadata_sha256": "AcquisitionService canonical SourceCandidate metadata digest",
        "whole_record_sha256": "EvidenceCandidate binds verified SourceSnapshot content",
        "whole_response_sha256": "source connector response_digest hashes exact response bytes",
        # Hash-pinned governance loading and its derived read/review pins.
        "document_sha256": "SourceRightsEvidence official-document capture digest",
        "manifest_sha256": "load_ops_bundle recomputes every pinned manifest",
        "bundle_index_sha256": "KnowledgeOpsBundle.index_sha256 canonical index digest",
        "governance_index_sha256": "KnowledgeOpsBundle.index_sha256 canonical index digest",
        "safety_boundary_sha256": "ReviewPacketBuilder canonical SafetyBoundary digest",
        # Exact ledger/ref bindings copied into immutable review/release objects.
        "subject_entry_sha256": "ReviewPacket exact LedgerRef binding",
        "expected_predecessor_sha256": "ReviewEvent append-only predecessor check",
        # Reviewer/author evidence and attestations have explicit producer/verifier contracts.
        "provenance_evidence_sha256": "AuthorProvenance evidence producer contract",
        "verification_evidence_sha256": "ReviewerVerifier identity-evidence contract",
        "reviewer_verification_evidence_sha256": "ReviewEvent reviewer snapshot binding",
        "reviewer_identity_assertion_sha256": "canonical reviewer identity assertion digest",
        "event_claim_sha256": "canonical ReviewEvent claim digest",
        "attestation_sha256": "ReviewerVerifier attestation producer/verifier contract",
    }
)
AUDITED_SHA256_FIELDS = frozenset(AUDITED_SHA256_FIELD_EVIDENCE)
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TECHNICAL_HASH_ID_PATTERNS = {
    "event_id": re.compile(r"^event-[0-9a-f]{20}$"),
    "attestation_id": re.compile(r"^(?:attest|fixture)-[0-9a-f]{32}$"),
}
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_CN_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_INTERNATIONAL_PHONE = re.compile(r"(?<!\w)\+\d[\d ()-]{7,}\d(?!\w)")
_CN_NATIONAL_ID = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\w)")
_LABELED_IDENTIFIER = re.compile(
    r"(?i)(?:patient|patient[ _-]?id|mrn|medical[ _-]?record|身份证|病历号|患者)\s*[:=]"
)
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def assert_deidentified_query_terms(terms: Sequence[str]) -> None:
    if not terms:
        raise KnowledgeOpsPolicyError("acquisition request requires controlled query terms")
    if len(terms) > 20:
        raise KnowledgeOpsPolicyError("acquisition request has too many query terms")
    for term in terms:
        normalized = " ".join(term.split())
        if not normalized or len(normalized) > 128:
            raise KnowledgeOpsPolicyError("query term must contain 1-128 characters")
        if normalized != term.strip():
            raise KnowledgeOpsPolicyError("query terms must be normalized before acquisition")
        if "http://" in normalized.lower() or "https://" in normalized.lower():
            raise KnowledgeOpsPolicyError("query terms cannot contain URLs")
        if _contains_sensitive_text(normalized):
            raise KnowledgeOpsPolicyError("query terms appear to contain personal data")


def assert_no_sensitive_data(value: object, *, path: str = "payload") -> None:
    """Reject common direct identifiers in structured staging payloads."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _FORBIDDEN_DATA_KEYS:
                raise KnowledgeOpsPolicyError(
                    f"patient/personal data key is prohibited at {path}.{key}"
                )
            if isinstance(item, str) and (
                normalized_key in _TECHNICAL_VALUE_KEYS
                or (
                    normalized_key in _TECHNICAL_HASH_ID_PATTERNS
                    and _TECHNICAL_HASH_ID_PATTERNS[normalized_key].fullmatch(item)
                    is not None
                )
                or (
                    normalized_key in AUDITED_SHA256_FIELDS
                    and _LOWER_SHA256.fullmatch(item) is not None
                )
            ):
                continue
            assert_no_sensitive_data(item, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            assert_no_sensitive_data(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _contains_sensitive_text(value):
        raise KnowledgeOpsPolicyError(
            f"payload text appears to contain personal data at {path}"
        )


def validate_url_against_policy(url: str, policy: SourcePolicy) -> str:
    """Return a canonical permitted URL or fail before connector access."""

    if any(ord(character) < 32 for character in url):
        raise KnowledgeOpsPolicyError("source URL contains control characters")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise KnowledgeOpsPolicyError(f"source URL is malformed: {exc}") from exc
    if parsed.scheme != "https":
        raise KnowledgeOpsPolicyError("source URL must use https")
    if parsed.username or parsed.password:
        raise KnowledgeOpsPolicyError("source URL cannot contain credentials")
    if parsed.fragment:
        raise KnowledgeOpsPolicyError("acquisition URL cannot contain a fragment")
    if port not in {None, 443}:
        raise KnowledgeOpsPolicyError("source URL may only use port 443")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise KnowledgeOpsPolicyError("source URL requires a host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise KnowledgeOpsPolicyError("source URL cannot use an IP literal")
    if host == "localhost" or host.endswith((".local", ".internal")):
        raise KnowledgeOpsPolicyError("source URL host is local or internal")

    allowed_hosts = {
        (urlsplit(str(origin)).hostname or "").lower().rstrip(".")
        for origin in policy.allowed_origins
    }
    permitted = host in allowed_hosts or (
        policy.allow_subdomains
        and any(host.endswith(f".{allowed}") for allowed in allowed_hosts)
    )
    if not permitted:
        raise KnowledgeOpsPolicyError(
            f"source host {host!r} is outside SourcePolicy {policy.policy_id}"
        )

    if _INVALID_PERCENT_ESCAPE.search(parsed.path) or _INVALID_PERCENT_ESCAPE.search(
        parsed.query
    ):
        raise KnowledgeOpsPolicyError("source URL contains an invalid percent escape")
    decoded_path = _fully_unquote(parsed.path, component="path")
    if any(ord(character) < 32 for character in decoded_path):
        raise KnowledgeOpsPolicyError("source URL path contains encoded control characters")
    if "\\" in parsed.path or "\\" in decoded_path:
        raise KnowledgeOpsPolicyError("source URL path cannot contain backslashes")
    if any(part in {".", ".."} for part in decoded_path.split("/")):
        raise KnowledgeOpsPolicyError("source URL path cannot contain traversal segments")
    if _contains_sensitive_text(decoded_path):
        raise KnowledgeOpsPolicyError("source URL path appears to contain personal data")

    try:
        query_pairs = parse_qsl(
            parsed.query, keep_blank_values=True, strict_parsing=True
        )
    except ValueError as exc:
        raise KnowledgeOpsPolicyError("source URL query is malformed") from exc
    query_keys = [key for key, _ in query_pairs]
    if len(query_keys) != len(set(query_keys)):
        raise KnowledgeOpsPolicyError("source URL query parameters must be unique")
    allowed_query = set(policy.allowed_query_parameters)
    for key, value in query_pairs:
        lowered = key.lower()
        if key not in allowed_query:
            raise KnowledgeOpsPolicyError(
                f"source URL query parameter {key!r} is not allowlisted"
            )
        if any(part in lowered for part in _SENSITIVE_QUERY_KEY_PARTS):
            raise KnowledgeOpsPolicyError("source URL query cannot contain credentials")
        decoded_value = _fully_unquote(value, component="query value")
        if (
            len(decoded_value) > 256
            or any(ord(character) < 32 for character in decoded_value)
            or _contains_sensitive_text(decoded_value)
        ):
            raise KnowledgeOpsPolicyError(
                f"source URL query value for {key!r} is unsafe or contains personal data"
            )

    canonical_netloc = host if port is None else f"{host}:{port}"
    return urlunsplit(("https", canonical_netloc, parsed.path or "/", parsed.query, ""))


def validate_public_peer_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise KnowledgeOpsPolicyError("transport peer address is not an IP") from exc
    if not address.is_global or any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    ):
        raise KnowledgeOpsPolicyError("transport peer address is not public")
    return address.compressed


def validate_transport_route(
    *,
    requested_url: str,
    redirect_urls: Sequence[str],
    peer_ips: Sequence[str],
    policy: SourcePolicy,
) -> tuple[str, ...]:
    if len(redirect_urls) > 3:
        raise KnowledgeOpsPolicyError("connector redirect limit exceeded")
    route = (requested_url, *redirect_urls)
    if len(peer_ips) != len(route):
        raise KnowledgeOpsPolicyError("transport must attest a peer IP for every hop")
    canonical = tuple(validate_url_against_policy(item, policy) for item in route)
    for peer_ip in peer_ips:
        validate_public_peer_ip(peer_ip)
    return canonical


def _contains_sensitive_text(value: str) -> bool:
    return any(
        pattern.search(value)
        for pattern in (
            _EMAIL,
            _CN_PHONE,
            _INTERNATIONAL_PHONE,
            _CN_NATIONAL_ID,
            _LABELED_IDENTIFIER,
        )
    )


def _fully_unquote(value: str, *, component: str) -> str:
    current = value
    for _ in range(4):
        decoded = unquote(current)
        if decoded == current:
            return decoded
        current = decoded
    raise KnowledgeOpsPolicyError(
        f"source URL {component} uses excessive nested percent encoding"
    )
