# Welcome to cft

This onboarding content is authored in Markdown so developers can update it easily.

## Why the data may be limited

- CloudFront inventory requires AWS credentials and permissions.
- Current-month usage depends on CloudWatch metrics being available for the distribution.
- Billing totals require a linked AWS Data Export / CUR 2.0 delivery to S3 (Parquet).
- Upload visibility can come from CloudWatch `BytesUploaded` or from CloudFront standard logs.

## Helpful shortcuts

- `r` — refresh data
- `Enter` — open focused distribution
- `Ctrl+P` or `b` — open configuration
- `q` — close screens / quit

## Quick setup hints

- Link a CUR/Data Export S3 bucket, prefix, and export name in the configuration screen.
- Enable `cloudfront_bytes_uploaded_metric = true` in the profile TOML for POST uploads without logs.
- Configure distribution-specific logging to S3 or CloudWatch Logs for upload visibility on WebSocket traffic.
- Profile-scoped settings live under `~/.cft/config/` and per-profile cache under `~/.cft/cache/<profile>/state.json`.

---

Edit this file to change the onboarding content shown on first run.
