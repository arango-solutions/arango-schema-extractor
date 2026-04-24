# Domains (ground truth specs)

This folder contains **domain descriptions** used for evaluation.

Each domain is defined as a `domain.json` file that serves as the ground-truth conceptual model:
- entities (names + example properties)
- relationship types (from/to + example properties)

These specs are used by:
- the **physical schema generator** (to create multiple ArangoDB physical schema variants + sample data)
- the **integration evaluation harness** (to compare analyzer outputs vs ground truth)

Included domains:
- `healthcare`
- `financial_fraud_detection`
- `insurance`
- `intelligence`
- `network_asset_management`

Add a new pack by creating `domains/<name>/domain.json`; the eval harness
auto-discovers it via `list_domains()`.

