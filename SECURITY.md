# Security Policy

## Supported status

The Global Hybrid AI Orchestrator is currently a public-alpha project.

The alpha is not represented as production-ready and should not be used for unattended consequential autonomy.

Security fixes are handled on a best-effort basis while the runtime contract and trust boundaries continue to mature.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities, exploit details, credentials, sensitive project data, or affected-system information in a public issue.

For the public repository, use GitHub Private Vulnerability Reporting when it is available:

1. Open the repository Security section.
2. Choose the private vulnerability reporting option.
3. Describe the affected version or commit, impact, reproduction conditions, and any proposed mitigation.

If private vulnerability reporting is not available, open a public issue containing no vulnerability details and request a private reporting channel from the maintainer.

## Scope

Security-relevant reports include, but are not limited to:

- bypass of human approval or authority boundaries;
- external-supervisor authority escalation;
- path traversal or arbitrary file access;
- command or verification-policy bypass;
- provenance or evidence tampering;
- unsafe state-transition acceptance;
- secrets or sensitive data exposed through logs, traces, errors, or model context;
- provider output being accepted as trusted state without required validation;
- unintended execution outside configured project or task boundaries.

## Disclosure expectations

Please allow reasonable time for validation and remediation before public disclosure.

Do not test vulnerabilities against systems, repositories, accounts, or data you do not own or have explicit authorization to assess.
