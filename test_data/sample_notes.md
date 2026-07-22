# Sample Intake Attachment (Markdown)

Use this file to test the plain-text/markdown attachment path (no
extraction library involved — read and decoded as UTF-8 directly).

- **Proposed API**: Partner Order Lookup Service
- **Requesting team**: Fulfillment Platform
- **Expected consumers**: Partner logistics integrations (~12 partners)
- **Data sensitivity**: PII (order + customer identifiers)
- **Planned auth**: OAuth2 client-credentials against the shared API gateway
- **Planned exposure**: versioned REST endpoint `/v1/orders/{id}/status`
