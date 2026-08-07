# Security Policy

## Supported version

Security fixes apply to the latest release on the default branch.

## Report a problem

Use GitHub private vulnerability reporting when the repository is public. Do not open a public issue for a suspected vulnerability.

Include:

- The affected skill, command, or generated target.
- A minimal synthetic example.
- The expected safe behavior.
- The observed behavior and impact.

Do not include credentials, customer files, live endpoint details, or vendor documents.

## Safety scope

This project generates read-only Modbus artifacts. A report is high priority if it can cause a write, broadcast, discovery scan, unbounded poll, wrong-device binding, credential disclosure, path escape, or unsafe generated script.
