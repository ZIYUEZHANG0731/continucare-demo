# Security policy

ContinuCare is an engineering prototype that uses synthetic data only. It is not a production clinical system and has no supported production release.

## Reporting a vulnerability

Please contact the maintainers privately through their GitHub profiles or GitHub's private vulnerability-reporting channel when available. Do not open a public issue containing:

- API keys, tokens, passwords or connection strings;
- real patient, clinical or personally identifiable information;
- exploitable details that have not yet been coordinated with the maintainers.

## Data and secret handling

- Never use real patient information in this repository or its demos.
- Keep credentials in a local ignored `.env` or a deployment secret store.
- Do not commit SQLite databases, recordings, generated exports or raw model responses.
- Rotate any credential immediately if it is exposed, even if the file or commit is later removed.

The prototype must remain fail-closed when model configuration, evidence or an approved clinical rule is unavailable.
