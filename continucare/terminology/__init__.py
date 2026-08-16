"""Versioned terminology retrieval for monitored and newly reported symptoms."""

from continucare.terminology.catalog import (
    RepositoryTerminologyBackend,
    SupplementalTerminologyBackend,
    TerminologyCatalog,
    load_glp1_symptom_catalog,
    terminology_catalog_sha256,
)


def load_supplemental_terminology_backend() -> SupplementalTerminologyBackend:
    """Return the locked synthetic composite used only for supplemental reports."""

    return SupplementalTerminologyBackend(
        fixed_catalog=load_cn_glp1_terminology_catalog(),
        dynamic_catalog=load_glp1_symptom_catalog(),
    )


def load_cn_glp1_terminology_catalog() -> TerminologyCatalog:
    """Load the governed CN whitelist without coupling promotion tooling to runtime."""

    from continucare.terminology.cn_glp1 import (
        load_cn_glp1_terminology_catalog as load_catalog,
    )

    return load_catalog()

__all__ = [
    "RepositoryTerminologyBackend",
    "SupplementalTerminologyBackend",
    "load_supplemental_terminology_backend",
    "TerminologyCatalog",
    "load_cn_glp1_terminology_catalog",
    "load_glp1_symptom_catalog",
    "terminology_catalog_sha256",
]
