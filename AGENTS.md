# AGENTS.md — cft CloudFront TUI/CLI Tool

## Project Summary

`cft` is a cross-platform interactive TUI application with CLI switches for analyzing Amazon CloudFront usage, distribution metadata, and billing-related cost/usage data.

The application should prioritize:

- CloudFront distribution discovery and display.
- Per-distribution operational usage from CloudWatch metrics.
- Cost and usage analysis from AWS Data Exports / CUR 2.0 Parquet files stored in S3.
- Optional CloudFront standard logging via CloudWatch Logs or S3 for upload visibility, especially for WebSocket proxy distributions where CloudWatch `BytesUploaded` is not reliable.
- Strong caching to minimize AWS API calls, S3 downloads, CloudWatch Logs bytes scanned, and user cost.
- Boto3 SDK usage instead of shelling out to AWS CLI, so the tool works on Termux/Android, Linux, Windows, and systems without AWS CLI installed.

Do **not** use the AWS Cost Explorer API in normal operation. `aws ce get-cost-and-usage` / boto3 Cost Explorer calls may cost money per request. Prefer Data Exports + local Parquet/DuckDB analysis.

---

## Recommended Tech Stack

- **TUI:** Textual
- **CLI:** Typer
- **Terminal UI / Formatting:** Rich
- **AWS SDK:** boto3 (always prefer boto3 and official docs)
- **Parquet + SQL:** DuckDB
- **Config:** `platformdirs` for paths + either `tomlkit` (preserve comments) or `pydantic-settings` (validation)
- **Cache:** JSON files with DistributionID as keys
- **Packaging:** `pyproject.toml`-based packaging; use `uv` tooling as the developer prefers alongside standard build backends
- **Tests:** `pytest` plus `botocore` `Stubber` for AWS interaction tests

Notes:
- Prefer small, focused libraries to keep the runtime small for Termux/Android compatibility.
- Keep configuration files human-editable (TOML) and validate them with `pydantic` or `pydantic-settings` where schema enforcement is helpful.

---

## Agent Operating Protocol

`AGENTS.md` is the authoritative implementation guide for coding agents. `CFT.md` is the research/design notebook and may contain exploratory notes, CLI experiments, screenshots, or older wording. When the two conflict, follow `AGENTS.md`; use `CFT.md` only as supporting context.

Before making multi-file or multi-path edits:

1. Inspect the current repo structure and relevant files before proposing architecture.
2. Create a short step-by-step plan using the available planning/todo tool, or state the plan briefly if no tool exists.
3. Keep changes incremental and testable; avoid broad rewrites unless the user explicitly asks for them.
4. Prefer pure functions, adapters, and small modules that can be unit tested without AWS credentials.
5. Preserve user changes in the worktree. Do not revert unrelated edits.

When changing code:

- Add or update focused `pytest` tests for the behavior changed.
- Use `botocore.stub.Stubber` or fixtures for AWS responses; never call live AWS in unit tests.
- Update this file when a recurring project rule is discovered.
- Update user-facing docs or examples when CLI behavior, config shape, or JSON output changes.
- Run the narrowest useful tests first, then broader tests when practical.

When adding dependencies:

- Keep runtime dependencies small and portable for Termux/Android, Windows, low-resource VPS.
- Prefer dependencies already named in this file unless there is a clear benefit.
- Avoid adding heavy frameworks, background services, or platform-specific packages for MVP behavior.
- Explain any new dependency in the final response or associated commit/PR notes.

## Documentation Lookup Rules

Use current official documentation before implementing or changing external APIs.

- For libraries, frameworks, SDKs, APIs, CLI tools, or cloud services, use Context7 MCP first when available.
- Start with `resolve-library-id` using the library name and the user question unless the user provides an exact `/org/project` library ID.
- Query docs with the selected library ID and the full user question.
- Prefer official AWS, boto3/botocore, Textual, Typer, Rich, DuckDB, Pydantic, platformdirs, and Python docs.
- For AWS APIs, verify boto3 method names, parameters, pagination, region requirements, return shapes, and potential request charges before implementing.
- If documentation cannot be fetched, state the limitation and avoid guessing high-risk AWS billing or mutation behavior.

The Context7 MCP and AWS MCP server(s) has been setup with relevant documentation for AWS and other libraries.

## Development Priorities

Build in this order unless the user explicitly reprioritizes:

1. Correct, cached CloudFront distribution discovery.
2. Current-month CloudWatch operational metrics for downloads and requests.
3. Data Export / CUR 2.0 local Parquet billing analysis.
4. Upload visibility from CloudFront standard logs.
5. Guided setup automation only after APIs and costs are verified.
6. CloudFront distribution creation only after a separate documentation review.

Favor MVP slices that provide useful CLI behavior and testable service layers before adding advanced TUI interactions.

## Core Principles for Agents

1. **Use official references before implementing AWS APIs.**
   - Use AWS, boto3 docs, Context7 MCP, and the AWS docs MCP server when available.
   - Do not invent boto3 method names, parameters, or return shapes.
   - When adding a new AWS call, check whether that API has request charges and document cost-relevant behavior in code or docs.

2. **Prefer boto3 over subprocess.**
   - Do not rely on `aws` CLI subprocess calls for core functionality.
   - The user may run this on Termux/Android where AWS CLI installation may be inconvenient.
   - CLI examples in `CFT.md` are reference shapes only; translate implementation to boto3.

3. **Cache aggressively.**
   - Assume AWS calls, CloudWatch Logs queries, S3 downloads, and bytes scanned may cost money.
   - Do not repeatedly query AWS if cached data is fresh enough.
   - All cache behavior should be overridable with a user-requested refresh.
   - Record cache TTLs, ETags, source timestamps, month keys, and last-checked timestamps where applicable.
   - When code changes cache semantics, add or update cache freshness and invalidation tests.

4. **Do not commit secrets.**
   - Never commit AWS credentials, `.env` secrets, profile credentials, downloaded billing files, or log files.
   - Respect `.gitignore` for `~/.cft`-style state and local test data.
   - Before committing, confirm no AWS credentials, downloaded Parquet files, local cache files, or logs are staged.
   - Add `.gitignore` entries for local cache and downloaded data if missing.

5. **Separate operational usage from billing truth.**
   - CloudWatch metrics are operational usage per distribution.
   - Data Exports / CUR 2.0 are billing/cost truth per account/profile.
   - CloudFront standard logs are optional deeper request/log visibility.

6. **Design for mobile terminals.**
   - The TUI must work on Termux and narrow terminal widths.
   - Tables must truncate gracefully and adapt to terminal width.
   - Use Textual + Rich responsive patterns and provide narrow and wide layout test cases.
   - Add visual-mode automated checks where possible, or unit tests that validate layout decisions and string widths.

7. **Keep side effects explicit.**
   - Default commands should read cached/local data when possible.
   - Networked AWS reads should be visible in command names, flags, logs, status messages, or refresh flows.
   - Mutating AWS actions require explicit user confirmation in TUI/CLI flows and separate tests for confirmation behavior.

8. **Keep JSON output stable.**
   - Treat non-interactive CLI JSON as an API.
   - Add fields without breaking existing keys when practical.

---

## High-Level Architecture

### Main Objects

```text
Account/Profile
  ├── CloudFront distributions
  ├── CloudWatch usage cache
  ├── Data Export / Parquet billing cache
  └── Local computed summaries
```

### Suggested Python Package Layout

```text
cft/
  main.py
  app.py
  config/
    paths.py
    settings.py
    profiles.py
  aws/
    session.py
    sts.py
    cloudfront.py
    cloudwatch.py
    s3.py
    logs.py
  data_exports/
    manifest.py
    downloader.py
    parquet_store.py
    queries.py
  cloudfront_logs/
    cloudwatch_logs.py
    s3_logs.py
    parsers.py
  models/
    account.py
    distribution.py
    usage.py
    billing.py
    cache.py
  tui/
    screens.py
    tables.py
    widgets.py
  cli/
    commands.py
    json_output.py
  cache/
    store.py
    policies.py
  utils/
    units.py
    time.py
    formatting.py
```

This structure is flexible. Keep modules small and testable.

---

## Configuration and Local Data

Use a local application directory:

```text
~/.cft
```

or the platform equivalent:

- Linux/macOS: `$XDG_CONFIG_HOME/cft`, `$XDG_CACHE_HOME/cft`, `$XDG_DATA_HOME/cft` where appropriate.
- Windows: use the user profile / app data equivalent.

Suggested config layout:

```text
~/.cft/
  config.toml
  profiles/
    default.toml
  cache/
    profile/
      distributions.json
  data_exports/
    <profile>/
      <distribution_id>.<mm>.parquet
```

AWS credentials remain in:

```text
~/.aws/config
~/.aws/credentials
```

Do not copy or store AWS access keys inside `~/.cft`.

Suggested `distributions.json` shape could mirror how data is displayed in the TUI
```json
{"last_updated":"2023-10-01T00:00:00Z","s3_data_export_etag":"etag123","usage_amount":1000,"bytes_downloaded":500,"bytes_uploaded":500,"requests":100,"distributions":[]}
```

Suggested per distribution shape:
```json
DistributionID: {"cw_last_updated","cw_upload","s3_last_updated","s3_upload"}
```

## AWS Profiles and Sessions

The app should support multiple AWS profiles.

Use boto3 session creation like:

```python
boto3.Session(profile_name=profile_name, region_name=region)
```

Behavior:

- Read configured AWS profiles.
- Use profile default region if available.
- CloudFront and CloudWatch CloudFront metrics should default to `us-east-1` where required.
- Allow manual region override for services where region matters.

Required basic identity call to resolve account ID:

- `sts.get_caller_identity()`

---

## CloudFront Distribution Discovery

Equivalent AWS CLI reference:

```bash
aws cloudfront list-distributions
```

Implement with boto3 CloudFront client.

Required fields:

- Distribution ID
- Comment
- DomainName
- Enabled
- Status
- LastModifiedTime
- PriceClass
- Aliases
- Origins
- DefaultCacheBehavior
- CacheBehaviors

Important notes:

- Distribution ID is the primary key.
- Comment is the practical user-facing name unless the user configures a local alias or uses tags.
- CloudFront console “Name” may not be available through distribution config.
- DomainName is present on distribution list/get-distribution, but not always inside `DistributionConfig` alone.

### Tags

Equivalent AWS CLI reference:

```bash
aws cloudfront list-tags-for-resource --resource arn:aws:cloudfront::<account-id>:distribution/<distribution-id>
```

Use tags for:

- user-friendly names
- identifying distributions created by this program
- future grouping/filtering

Do not require tags for MVP.

---

## Pricing Mode: PAYG vs Flat Rate Free Plan

The app should allow the user to specify a distribution pricing type.

Supported local enum:

```text
payg
flat_free
unknown
```

Usage limit assumptions from current research:

- PAYG download free tier is pooled per account.
- Flat-rate/free plan distributions appear to have a per-distribution/free-plan allowance.
- Flat-rate plan accounting appears differently in CUR/Data Exports.

The app should not hardcode final billing policy without user override and documentation.

---

## CloudWatch Metrics for Per-Distribution Usage

Use CloudWatch for operational per-distribution metrics.

CloudFront metrics require:

```text
Namespace: AWS/CloudFront
Region: us-east-1
Dimensions:
  DistributionId=<distribution_id>
  Region=Global
```

Useful metrics:

- `BytesDownloaded`
- `Requests`
- `TotalErrorRate`
- `4xxErrorRate`
- `5xxErrorRate`

`BytesUploaded` is not reliable for WebSocket proxy payload visibility. Do not use it as authoritative upload for WebSocket traffic.

### Query behavior

For monthly totals:

- Query from beginning of current billing month to now.
- Use a period large enough for monthly aggregation or daily buckets and sum locally.
- Cache the result.

Cache policy:

- Default cache TTL should avoid excessive queries.
- Refresh manually on user request.
- Store cache by profile, distribution ID, metric name, start/end period, and timestamp.

---

## Data Exports / CUR 2.0 Parquet Billing Analysis

Do not use Cost Explorer API for normal billing analysis.

Use AWS Data Exports / CUR 2.0 files delivered to S3, downloaded locally, and queried with a local Parquet engine such as DuckDB.

### Required Data Export Columns

Minimum columns for CloudFront billing analysis:

- `line_item_line_item_type`
- `line_item_net_unblended_cost`
- `line_item_usage_amount`
- `line_item_usage_start_date`
- `line_item_usage_end_date`
- `line_item_usage_type`
- `line_item_product_code`

The tool will need to automatically create a data export in the future, for now it's safe to assume a data export which a user can link already have these fields.

### Product Code Notes

Observed useful product codes:

- PAYG CloudFront: `AmazonCloudFront`
- Flat rate plan: `CloudFrontPlans`
- S3: service-specific S3 product codes
- CloudWatch: service-specific CloudWatch product codes

### Usage Type Notes

PAYG CloudFront examples:

```text
<Region>-DataTransfer-Out-Bytes
<Region>-DataTransfer-Out-OBytes
<Region>-Requests-HTTP-Proxy
```

Flat-rate/free plan observed example:

```text
Global-CloudFrontPlan-Free
```

Interpretation:

- `*-DataTransfer-Out-Bytes` = viewer download / CloudFront to internet.
- `*-DataTransfer-Out-OBytes` = CloudFront edge to origin transfer.
- `*-Requests-HTTP-Proxy` = HTTP proxy requests.

Always filter billing analysis with:

```sql
WHERE line_item_line_item_type = 'Usage'
```

Do not apply free tier credit as these are limited time, or tax or refunds.

### Simple Aggregate Query

```sql
SELECT
  line_item_product_code,
  line_item_usage_type,
  SUM(line_item_net_unblended_cost) AS total_cost,
  SUM(line_item_usage_amount) AS total_usage
FROM data
WHERE line_item_line_item_type = 'Usage'
GROUP BY ALL
ORDER BY line_item_product_code ASC, total_cost DESC;
```

### CloudFront PAYG Aggregate Query

```sql
SELECT
  line_item_usage_type,
  SUM(line_item_usage_amount) AS usage_amount,
  SUM(line_item_net_unblended_cost) AS net_cost
FROM data
WHERE line_item_line_item_type = 'Usage'
  AND line_item_product_code = 'AmazonCloudFront'
GROUP BY line_item_usage_type
ORDER BY net_cost DESC;
```

### Date Validation

For downloaded exports:

- Check earliest `line_item_usage_start_date`.
- Check latest `line_item_usage_end_date`.
- Display the last updated time based on the information

---

## Data Export S3 Manifest and Download Strategy

AWS Data Export output may contain manifests and Parquet files.

Expected manifest path pattern:

```text
<bucket>/<path>/<export_name>/BILLING_PERIOD=<yyyy>-MM/metadata/<export_name>-Manifest.json
```

Use S3 `head_object` on the manifest and cache its ETag.

Algorithm:

1. Compute expected manifest key for the current billing period.
2. `head_object` the manifest.
3. If cached manifest ETag matches remote ETag, skip download.
4. If changed:
   - download manifest
   - parse data file list
   - head/download changed Parquet files
   - update local cache
5. Query local Parquet files.

Important:

- Data Export can lag many hours.
- Do not download repeatedly, check interval: about 4 hours, user-overridable.

---

## CloudFront Standard Logging

Standard logging is optional and used to fill the WebSocket upload visibility gap.

Useful fields:

- `DistributionId`
- `date`
- `time`
- `sc-bytes`
- `cs-bytes`
- `c-ip`
- `c-port`
- `x-edge-location`
- `x-edge-detailed-result-type`

For WebSocket upload approximation:

- Use `cs-bytes` (upload to origin)

---

## CloudWatch Logs for CloudFront Logs

CloudWatch Logs can be used for CloudFront standard logs if configured.

Useful Logs Insights query:

```sql
stats sum(`cs-bytes`) as uploads by DistributionId
```

Equivalent CLI shape:

```bash
aws logs start-query \
  --log-group-name "cloudfrontlogs" \
  --start-time <unix_start> \
  --end-time <unix_end> \
  --query-string 'stats sum(`cs-bytes`) as uploads by DistributionId'
```

Then poll:

```bash
aws logs get-query-results --query-id <queryId>
```

Implementation notes:

- Use boto3 Logs client.
- Poll until `status == Complete` or timeout.
- Cache results incrementally by distribution and month.

### Incremental Cache Algorithm for Uploads (applicable for both CloudWatch Logs and S3 logs)

For each distribution:

1. Read cache entry.
2. If no cache or previous month:
   - start = beginning of current month
   - cached upload = 0
3. If last update is less than the minimum refresh interval:
   - return cached upload
4. If cache exists for current month:
   - start = last_updated
   - query only new range
5. Add new `cs-bytes` to cached total.
6. Set `last_updated = now`.
7. Persist cache.

Use the same strategy for CloudWatch metrics where appropriate.

---

## S3 CloudFront Logs

S3 log location example:

```text
s3://<bucket_name>/AWSLogs/<account-id>/CloudFront/
```

Observed file pattern:

```text
<distribution_id>.yyyy-mm-dd.<random>.parquet
```

Algorithm:

1. User links S3 log bucket/prefix.
2. List objects under prefix.
3. Compare object `LastModified` to cache timestamp.
4. Download only new/changed files.
5. Optionally combine local Parquet by distribution/month.
6. Query local Parquet for aggregate `cs-bytes`.
7. Cache aggregate upload values.

Remote deletion can be an advanced explicit user action only.

Recommended lifecycle for user-created log buckets:

- Retain 30 days. Then permanently delete

---

## TUI Requirements

The main interface should show one or more AWS profiles.

At top level, show current-month summary:

- current date/time
- CloudFront billing usage/cost where available
- download
- upload
- HTTP proxy requests or other usage types
- last updated time

Some fields may be blank or `-` if not configured.

### Distribution Table Columns

The distribution table should adapt to terminal width.

Columns:

- `Dist`: distribution ID, truncatable, minimum 4–6 chars
- `Comment`: human-friendly name, truncatable, minimum 7 chars
- `Type`: `Free`, `PAYG`, or `?`
- `URL`: CloudFront domain, truncatable, minimum 4–6 chars
- `On`: enabled/deployed state, compact colored marker
- `Log`: logging enabled/linked state, compact marker
- `UL/DL`: upload/download GB
- `Req`: request count using compact formatting

Formatting examples:

- GB with room: `1.234 GB`
- narrow display: `1.2`
- requests: `1234`, `1.2K`, `1.234K`, `1.2M`

Target terminal widths:

- Termux/mobile: 60–120 columns
- desktop: 45–225 columns

Clicking/selecting a distribution should open a dismissible detail view with:

- full ID
- ARN
- comment
- tags
- domain
- status
- enabled state
- origins
- cache behaviors
- usage details
- billing details where available
- logging configuration
- actions such as enable/disable, refresh, configure logs

Dangerous actions like delete must require confirmation.

---

## CLI Requirements

The tool should support non-interactive switches for automation.

Examples to implement over time:

```bash
cft
cft --profile default
cft distributions --json
cft usage --profile default --json
cft billing --profile default --json
cft refresh --profile default
cft logs uploads --profile default --json
```

JSON output should be stable and documented.

---

## Build Stages

### Stage 1 — Basic TUI Distribution Browser

Requirements:

- Load AWS profile/session.
- Resolve account ID.
- List CloudFront distributions table.

### Stage 2 — CloudWatch Per-Distribution Usage

Requirements:

- Query current-month `BytesDownloaded` and `Requests`.
- Cache per distribution/metric/month.
- Display usage in TUI table.
- Support manual refresh.

### Stage 3 — Data Export Parquet Billing

Requirements:

- Let user link an existing Data Export S3 bucket/prefix/export name.
- Download manifest only if ETag changed.
- Download changed Parquet files.
- Query locally using DuckDB or equivalent.
- Display CloudFront PAYG billing aggregate.
- Detect flat-rate/free plan rows where present.

### Stage 4 — Optional Upload Visibility via Logs

Requirements:

- Link CloudWatch Logs log group or S3 log bucket/prefix.
- Query `cs-bytes` by distribution.
- Cache incrementally.
- Display upload estimates.
- Show bytes scanned / cost implication where available.

### Stage 5 — Guided Setup Automation

Future feature:

- Create required S3 buckets.
- Configure Data Exports.
- Configure CloudFront logs to S3 or CloudWatch Logs.
- Configure lifecycle policies.

Only implement after verifying boto3 APIs and costs.

### Stage 6 — CloudFront Distribution Creation

Future feature:

- Programmatic distribution creation, likely PAYG only at first.
- User-specified origins and safe defaults.
- Requires deeper AWS documentation review.

---

## Development Workflow

- Follow test-driven development where practical.
- Use meaningful branches and commit messages.
- Do not commit secrets or local cache files.
- Provide debug mode and reload-friendly development workflow.

Example development alias:

```bash
alias cft-dev='python cft/main.py --reload --debug'
```

Production may use:

- installed Python package entry point
- PyInstaller binary
- script in `$PATH`

---

## Testing Strategy

Write unit tests for:

- AWS response normalization
- distribution model parsing
- CloudWatch datapoint summing
- cache freshness logic
- manifest ETag comparison
- Parquet query SQL generation
- unit conversion and formatting
- table truncation logic
- Logs Insights result parsing

Use fixtures for AWS responses. Do not require real AWS calls in normal unit tests.

Integration tests that hit AWS should be explicitly marked and disabled by default.

---

## Important Warnings for Agents

- Do not use AWS Cost Explorer API unless explicitly asked by the user.
- Do not assume CloudWatch `BytesUploaded` captures WebSocket payload uploads.
- Do not assume CloudFront pricing mode is exposed in distribution config.
- Do not assume flat-rate plan usage behaves the same as PAYG in Data Exports.
- Do not repeatedly query CloudWatch Logs without checking cache and bytes scanned.
- Do not delete S3 logs or billing exports unless user explicitly confirms.
- Do not hardcode the user’s account ID, bucket names, distribution IDs, or profile names.
- Do not commit downloaded Parquet files.

---

## Glossary

- **PAYG**: Pay-as-you-go CloudFront metered pricing.
- **Flat Free**: Newer CloudFront flat-rate/free plan mode, user-specified unless detectable via documented data.
- **Out-Bytes**: CloudFront data transfer out to viewers/internet.
- **Out-OBytes**: CloudFront data transfer out to origin.
- **CUR/Data Export**: AWS billing data exported to S3, preferably Parquet.
- **CloudWatch Metrics**: Operational per-distribution metrics.
- **CloudFront Logs**: Request-level logs, useful for deeper traffic analysis but not full WebSocket frame accounting.
