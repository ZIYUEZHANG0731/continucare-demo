"""Offline-testable official metadata connector contracts.

Nothing in this package grants operational acquisition network access.  The
only socket implementation requires an opaque, explicitly issued live-
validation capability and is kept separate from ``AcquisitionService``.
"""

from continucare.knowledge.ops.source_connectors.contracts import (
    ControlledRequest,
    EndpointPolicy,
    FakeMetadataTransport,
    MetadataResponse,
    MetadataTransport,
)
from continucare.knowledge.ops.source_connectors.errors import (
    ConnectorErrorCode,
    ConnectorFailure,
    ErrorDisposition,
    disposition_for,
)
from continucare.knowledge.ops.source_connectors.flags import (
    KNOWLEDGE_LIVE_VALIDATION_ENV,
    KnowledgeEgressPermit,
    issue_knowledge_egress_permit,
    knowledge_live_validation_enabled,
)
from continucare.knowledge.ops.source_connectors.parsing import (
    ParserLimits,
    XmlNode,
    parse_bounded_json,
    parse_bounded_xml,
)
from continucare.knowledge.ops.source_connectors.transport import (
    SecureMetadataTransport,
)

__all__ = [
    "ControlledRequest",
    "ConnectorErrorCode",
    "ConnectorFailure",
    "EndpointPolicy",
    "ErrorDisposition",
    "FakeMetadataTransport",
    "KNOWLEDGE_LIVE_VALIDATION_ENV",
    "KnowledgeEgressPermit",
    "MetadataResponse",
    "MetadataTransport",
    "ParserLimits",
    "SecureMetadataTransport",
    "XmlNode",
    "disposition_for",
    "issue_knowledge_egress_permit",
    "knowledge_live_validation_enabled",
    "parse_bounded_json",
    "parse_bounded_xml",
]
