# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the application (app stays running; Ctrl+C to stop — Maven prints BUILD FAILURE on stop, which is normal)
mvn spring-boot:run

# Build a runnable JAR (preferred for clean start/stop)
mvn package -DskipTests
java -jar target/hospital-management-system-1.0.0.jar

# Compile only (triggers MapStruct code generation into target/generated-sources/annotations/)
mvn compile

# Run all tests
mvn test

# Run a single test class
mvn test -Dtest=PatientServiceTest

# Run a single test method
mvn test -Dtest=PatientServiceTest#createPatient_Success

# Clean and recompile (required when MapStruct mappers behave unexpectedly)
mvn clean compile
```

**Prerequisites:** MySQL 8 must be running on `localhost:3306`. Update `src/main/resources/application.yml` if your MySQL root password differs from `root`. The database `hms_db` is auto-created on first startup.

## URLs (when running)

| Resource | URL |
|---|---|
| Swagger UI | `http://localhost:8080/api/v1/swagger-ui/index.html` |
| OpenAPI JSON | `http://localhost:8080/api/v1/api-docs` |
| Actuator health | `http://localhost:8080/api/v1/actuator/health` |

All non-auth endpoints require `Authorization: Bearer <token>`. Get a token via `POST /api/v1/auth/login`.

> **Note:** `spring-boot-starter-actuator` is **not** declared in `pom.xml` — the `/actuator/health` URL above returns 404. The `w2-validator` smoke check falls back to the Swagger UI (HTTP 200) as a workaround.

## Architecture

Spring Boot 3.2.3 / Java 17 REST API. Base package: `com.hms`. Every business domain is a self-contained sub-package with the same internal layout:

```
<module>/
  controller/     REST layer — delegates directly to service, no logic
  service/        Interface
  service/impl/   Implementation — all business logic lives here
  repository/     Spring Data JPA interface
  entity/         JPA entity (extends BaseEntity)
  dto/            Request + Response POJOs (never expose entities)
  mapper/         MapStruct interface (Spring-managed bean)
  validator/      Domain-specific validation helpers (optional)
```

Modules: `auth`, `patient`, `doctor`, `appointment`, `medicalrecord`, `prescription`, `billing`  
Cross-cutting: `common` (BaseEntity, ApiResponse, enums), `exception`, `security`, `config`

**Domain relationships** — `Appointment` links `Patient` ↔ `Doctor`. `Prescription` and `MedicalRecord` also reference both. `Billing` is standalone (created per patient visit).

### Key design decisions

**BaseEntity** (`common/entity/BaseEntity.java`) — all entities extend this. It provides `createdAt`, `updatedAt`, `createdBy`, `updatedBy` via Spring Data JPA auditing. `AuditConfig` resolves the current auditor from the Spring Security context.

**MapStruct + Lombok interaction** — entities use `@Builder`, but Lombok's `@Builder` does not include fields inherited from `BaseEntity`. As a result, all `toEntity()` mapper methods must include `@BeanMapping(builder = @Builder(disableBuilder = true))` to force MapStruct to use setters instead of the builder. Omitting this causes a compile error (`Unknown property "createdAt" in result type XxxBuilder`). MapStruct is configured with `defaultComponentModel=spring` — all mapper interfaces are injected as Spring beans.

**JWT flow** — `JwtAuthenticationFilter` extracts the Bearer token, validates it via `JwtTokenProvider` (JJWT 0.12.x API), loads `UserPrincipal` from `CustomUserDetailsService`, and sets the `SecurityContext`. Roles are stored as `ROLE_<ENUM_NAME>` (e.g. `ROLE_ADMIN`). Method-level security (`@PreAuthorize`) uses `hasRole('ADMIN')` which matches against the `ROLE_` prefix automatically.

**Appointment conflict detection** — `AppointmentRepository` has two named queries (`findConflictingAppointment` / `findConflictingAppointmentExcluding`) that check for overlapping doctor time slots while excluding `CANCELLED` and `NO_SHOW` statuses. The service calls the `Excluding` variant during reschedule so the appointment being rescheduled does not conflict with itself.

**ApiResponse wrapper** — all controllers return `ApiResponse<T>` (from `common/dto/ApiResponse.java`). Use the static factory methods: `ApiResponse.success(data)`, `ApiResponse.success(message, data)`, `ApiResponse.error(message)`.

**Exception handling** — throw domain exceptions from service layer; `GlobalExceptionHandler` (`@RestControllerAdvice`) maps them:

| Exception | HTTP status |
|---|---|
| `ResourceNotFoundException` | 404 |
| `AppointmentConflictException` | 409 |
| `DoctorUnavailableException` | 422 |
| `BusinessValidationException` | 400 |

**`ddl-auto: update`** — schema is managed automatically by Hibernate. No migration tool is configured.

**Jackson configuration** — `default-property-inclusion: non_null` is set globally; null fields are omitted from all JSON responses. `write-dates-as-timestamps: false` means `LocalDateTime` fields serialize as ISO-8601 strings.

**Pagination** — list endpoints accept `?page=0&size=10` query params. Controllers construct `PageRequest.of(page, size, Sort.by(...))` and pass it to repository methods returning `Page<T>`. Sort field and direction are hardcoded per endpoint.

**Prescription aggregate** — `Prescription.items` uses `@OneToMany(cascade = ALL, orphanRemoval = true)`. Removing an item from the in-memory list and saving the parent will physically DELETE the row — manage the collection through the parent only.

**Soft delete** — `Patient` uses an `active` boolean (`deactivatePatient()` sets it to `false`, no SQL DELETE). `Doctor` uses `available` for the same pattern. Note: `UserPrincipal.isEnabled()` always returns `true`, so a deactivated `User.active = false` record can still authenticate.

**Business key generation** — `generatePatientCode()`, `generateAppointmentNumber()`, and `generateBillNumber()` all use `repository.count() + 1` at the application layer. This is not concurrency-safe; duplicate keys under concurrent inserts will surface as DB unique-constraint violations. The `AtomicLong` fields that exist in service impls are unused.

Business code formats:

| Entity | Format | Example |
|---|---|---|
| Patient | `PAT-YYYYMM-NNNN` | `PAT-202601-0001` |
| Doctor | `DOC-SPEC-YYYY-NNNN` | `DOC-CAR-2026-0001` |
| Appointment | `APT-YYYYMMDD-NNNNN` | `APT-20260615-00001` |
| Prescription | `RX-YYYYMMDD-NNNNN` | similar pattern |

**JWT does not embed roles** — the token payload only stores the `subject` (username). Every authenticated request triggers a `UserRepository` query via `CustomUserDetailsService` to reload roles. There is no `/auth/refresh` endpoint despite `app.jwt.refresh-expiration` being configured.

**`PatientValidator`** (`com.hms.patient.validator`) exists but is never called from `PatientServiceImpl`. It is effectively dead code.

## Conventions

**Transactions** — all write service methods are `@Transactional`; all read methods are `@Transactional(readOnly = true)`.

**Security roles** — `ADMIN`, `DOCTOR`, `RECEPTIONIST`, `PATIENT`. Use `hasRole('ADMIN')` (not `hasAuthority('ROLE_ADMIN')`) in `@PreAuthorize`. Endpoint access rules from `SecurityConfig`:

| Endpoint pattern | Allowed roles |
|---|---|
| `/auth/**`, Swagger, `/actuator/**` | Public |
| `GET /patients/**` | ADMIN, DOCTOR, RECEPTIONIST |
| `* /patients/**` | ADMIN, RECEPTIONIST |
| `GET /doctors/**` | ADMIN, DOCTOR, RECEPTIONIST, PATIENT |
| `* /doctors/**` | ADMIN only |
| `POST /appointments/**` | ADMIN, RECEPTIONIST, PATIENT |
| `GET /appointments/**` | ADMIN, DOCTOR, RECEPTIONIST, PATIENT |
| `* /appointments/**` | ADMIN, RECEPTIONIST |
| `/medical-records/**` | ADMIN, DOCTOR, RECEPTIONIST |
| `/prescriptions/**` | ADMIN, DOCTOR |
| `/billing/**` | ADMIN, RECEPTIONIST |

**Enums** — all enum fields on entities are persisted as `EnumType.STRING`.

## Intentionally vulnerable dependencies

This is a GHAS/Dependabot demo project. The following dependencies are declared in `pom.xml` to generate Dependabot alerts and are **not used in application code**:

| Dependency | Version | Purpose |
|---|---|---|
| `log4j-core` | 2.14.1 | CVE-2021-44228 (Log4Shell), CVE-2021-45046, CVE-2021-45105, CVE-2021-44832 |
| `commons-collections` | 3.2.1 | CVE-2015-7501, CVE-2015-6420 (deserialization RCE) |
| `jackson-databind` | 2.13.2 | CVE-2020-36518, CVE-2022-42003, CVE-2022-42004 |
| `guava` | 29.0-jre | CVE-2020-8908, CVE-2023-2976 |
| `gson` | 2.8.5 | CVE-2022-25647 |
| `commons-text` | 1.9 | CVE-2022-42889 (Text4Shell — RCE via StringSubstitutor) |
| `snakeyaml` | 1.30 | CVE-2022-1471 (RCE via unsafe deserialization) |
| `h2` | 1.4.200 | CVE-2021-42392, CVE-2022-23221 (unauthenticated RCE via JNDI) |
| `xstream` | 1.4.17 | CVE-2021-39139 and ~17 others (RCE via unsafe type processing) |
| `netty-all` | 4.1.72.Final | CVE-2021-43797 (HTTP request smuggling) |

Do not upgrade these without understanding the GHAS workflow impact.

**Known caveats:**
- `snakeyaml 1.30` overrides Spring Boot's BOM-managed safe version — `w2-validator` may need a `<dependencyManagement>` override if the fix doesn't take effect transitively.
- `h2 1.4.200` is declared without `<scope>test</scope>`, placing it on the runtime classpath. Spring Boot may attempt to auto-configure an H2 datasource — watch for startup conflicts during `w2-validator`'s smoke check.
- `xstream 1.4.17` alone generates ~18 Dependabot alerts due to the large number of individually tracked CVEs.

## Tests

All tests are service-layer unit tests using Mockito (`@ExtendWith(MockitoExtension.class)`) — no `@SpringBootTest`, no H2, no Testcontainers. Collaborators are `@Mock`; the class under test uses `@InjectMocks`. Assertions use AssertJ (`assertThat`, `assertThatThrownBy`). Coverage exists for `patient`, `appointment`, `billing`, `doctor`, and `auth` services only. The `medicalrecord` and `prescription` modules have no tests yet.

## GHAS Vulnerability Management

### Prerequisites

**GitHub CLI** — run `gh auth login` once (no token file needed; `fetch_alerts.sh` uses keyring auth).

**Jira API** — All Jira operations use `jira_ticket_manager.py` (pure Python, no MCP). Setup is required once:
1. `pip install requests python-dotenv pyyaml` (one-time)
2. Fill in `.env` at repo root:
   ```
   JIRA_BASE_URL=https://tanishqshrivas.atlassian.net   # also accepted as JIRA_URL
   JIRA_EMAIL=<your-atlassian-account-email>
   JIRA_API_TOKEN=<your-atlassian-api-token>
   ```
3. Verify auth: `python .claude/scripts/jira_ticket_manager.py search --project HMS --labels GHAS`

**Python script operations (all Jira ops):**

| Operation | Subcommand | Agents |
|---|---|---|
| Search tickets (JQL) | `jira_ticket_manager.py search --jql "..."` | w1-jira-manager, alert-ingestion-orchestrator |
| Get issue details | `jira_ticket_manager.py get --ticket HMS-XX` | w2-verifier, vuln-resolver-orchestrator |
| Apply transition | `jira_ticket_manager.py transition --ticket --name` | w2-reporter, alert-ingestion-orchestrator |
| Post comment | `jira_ticket_manager.py comment --ticket --body-file` | w2-reporter |
| Create ticket | `jira_ticket_manager.py create --project --service --csv` | w1-jira-manager |
| Update description | `jira_ticket_manager.py update-description --ticket --service --csv` | w1-jira-manager |

### Fixed Configuration (hardcoded in all agents — never ask the user)

| Setting | Value |
|---|---|
| Repo | `tanishq-sh17/HMS` |
| Jira Site URL | `https://tanishqshrivas.atlassian.net` |
| Jira Project Key | `HMS` |
| Repo root | `C:\Users\TanishqShrivas\DummyProj\GHAS-dummy-projects\HMS` |

### Multi-Agent Orchestration

A two-workflow, multi-agent system lives in `.github/agents/` (mirrored in `.claude/agents/`) for automated Dependabot vulnerability remediation.

```
.claude/
  agents/                             ← canonical agent definitions (used by Claude Code)
    alert-ingestion-orchestrator.md
    w1-fetcher.md
    w1-sorter.md                      ← DEPRECATED: no longer invoked; service grouping done inline by orchestrator
    w1-jira-manager.md
    vuln-resolver-orchestrator.md
    w2-context-builder.md
    w2-planner.md                     ← change plan + proposed diff (replaces w2-rca)
    w2-fixer.md
    w2-validator.md
    w2-verifier.md                    ← comprehensive verification before PR creation (runs before human review)
    w2-reporter.md
  scripts/                            ← scripts invoked by agents (mirrored from .github/scripts/)
    fetch_alerts.sh                   ← active: gh CLI → timestamped CSV (all alert types)
    jira_ticket_manager.py            ← Python script — handles ALL Jira operations
    validate_config.py                ← validates ghas-w1-config.yml / ghas-w2-config.yml at startup

.github/agents/                       ← mirror of .claude/agents/ (kept in sync manually)
```

**Two-folder setup:** `.github/agents/` is loaded by GitHub Copilot CLI; `.claude/agents/` is the identical mirror loaded by Claude Code. Keep them in sync when modifying agent definitions.

### Workflow 1 — Alert Ingestion

Steps run in order; any failure stops the workflow.

1. **`w1-fetcher`** (Sub-Agent 1) — runs **once**; `fetch_alerts.sh` handles all services via its own internal hardcoded loop. Runs the script via Git Bash (`chmod +x` + single timestamped output path); fetches Dependabot, Code Scanning, and Secret Scanning alerts for all services; writes **one consolidated CSV**. If `ALERT_COUNT=0` the fetcher emits that count and exits successfully — no tickets are closed.
2. **Orchestrator runs Step 1.5 inline** — parses the consolidated CSV to count alert rows per configured service; derives `$NONZERO_ALERT_SVCS` and `$ZERO_ALERT_SVCS`; sets `$SERVICE_NAMES = $NONZERO_ALERT_SVCS -join ','`. Zero-alert services are logged in the Final Output for visibility only — no tickets are created or closed for them. **`@w1-sorter` is no longer invoked as a sub-agent.**
3. **`w1-jira-manager`** (Sub-Agent 2, active) — searches Jira via **`jira_ticket_manager.py search`** using a JQL query filtered by labels, service, status, and optionally `parent_jira`; if an active ticket exists (status in `skip_statuses`), compares CVEs and **updates its description in-place** via `update-description`; if no active ticket exists (or prior is Done/Testing/QA), creates a fresh consolidated ticket; updates CSV with Jira key + status

**Multi-service config** — add one entry per service under the root-level `services` key (not under `environment`):
```yaml
services:
  - name: HMS               # display name used in Jira labels and CSV
    github_repo: HMS        # GitHub repo name (may differ from service name)
  - name: BillingService
    github_repo: billing-svc
```
`environment.service_name` is the single-service fallback used only when `services` is absent.

> **Testing with multiple services in the same repo** — multiple service entries can share the same `github_repo`. Each gets its own Jira ticket under a different service name. Alerts will be identical (same GitHub repo), but the full per-service Jira management flow is exercised.

**W1 Sub-agent reference:**

| # | Sub-agent | Status | Jira tooling |
|---|---|---|---|
| 1 | `@w1-fetcher` | Active | — |
| 2 | `@w1-jira-manager` | Active | Python `search`, `get`, `create`, `update-description` |
| — | `@w1-sorter` | **Deprecated** — not invoked | — |

**Sub-agent invocation pattern:** WF1 orchestrator loads config once in Step 0, then passes specific values to each sub-agent as explicit variables (`<PLACEHOLDER>` syntax).

| Sub-agent | Variables passed by orchestrator |
|---|---|
| `@w1-fetcher` | `CONFIG_PATH`, `REPO_ROOT`, `GIT_BASH`, `GH_CMD`, `PYTHON_CMD`, `FETCH_SCRIPT_UNIX`, `REPO_ROOT_UNIX`, `CSV_GLOB`, `REPO_OWNER`, `REPO_NAME` |
| `@w1-jira-manager` | `CONFIG_PATH`, `CSV_PATH`, `SERVICE_NAMES`, `SKIP_STATUSES`, `PYTHON_CMD`, `JIRA_SCRIPT`, `JIRA_PROJECT`, `BASE_LABEL`, `CSV_GLOB`, `PARENT_JIRA`, `SEARCH_LABELS` |

**Jira ticket title format:** `Address GHAS vulnerabilities for <SERVICE_NAME> [Critical-<N>, High-<N>, Medium-<N>, Low-<N>]`

**CSV columns (0-indexed):** `service` | `type` | `ghsa_id` | `cve_id` | `title` | `severity` | `created` | `due` | `url` | `Application` | `nonCompliant` | `ageDays` | **`jira_key`** | **`jira_status`**

**Jira ticket table columns** (configured via `jira.ticket_table_columns` in `ghas-w1-config.yml`): The `nonCompliant` column renders as **"Compliance Status"** in the ticket — CSV value `0` → "Compliant" (green), `1` → "Non-Compliant" (red, bold). Raw numbers are never shown.

### Workflow 2 — Vulnerability Resolver

Only input needed: **Jira ticket ID** (e.g. `HMS-16`); everything else is fixed config. Four retry counters with human escalation (max attempts configurable via `retry_limits` in `ghas-w2-config.yml`, default 3).

**W2 Sub-agent reference:**

| # | Sub-agent | Step | Jira tooling |
|---|---|---|---|
| 1 | `@w2-context-builder` | Step 1 | — |
| 2 | `@w2-planner` | Step 3 | — |
| 3 | `@w2-fixer` | Step 5a (loop) | — |
| 4 | `@w2-validator` | Step 5b (loop) | — |
| 5 | `@w2-verifier` | Step 6 | Python `get` |
| 6 | `@w2-reporter` | Step 9 | Python `comment`, `transition` |

0. **Config validation + Jira ticket validation** — loads `ghas-w2-config.yml`, validates required fields; validates ticket ID matches project key and fetches labels via **`jira_ticket_manager.py get`** to confirm it belongs to the intended service; all sub-agent variables resolved here once; aborts on failure
1. **`w2-context-builder`** (Sub-Agent 1) — fetches open alerts; discovers **all `pom.xml` files** in the project (excludes `target/`), reads each one; reads latest CSV for context; classifies each dependency as inline / property-backed / BOM-managed and records which file declares the version (`Declared in:` field in CONTEXT_MAP); audits sibling group consistency for `jjwt-*`, `log4j-*`, `jackson-*` across all pom files
2. **Feature branch created** — aborts if working tree is dirty; before any file is modified (named `{jira_id}-GHAS-{primary_package}[-and-N-more]`)
3. **`w2-planner`** (Sub-Agent 2) — scans source files to find which vulnerable packages are actually imported; generates CHANGE_PLAN with proposed `pom.xml` diff and breakage risk; supports re-planning when user gives feedback (**Plan Revision counter**, max per `retry_limits.plan_revision_max`)
4. **User approves plan** — checks `auto_approve_minor`/`auto_approve_critical` flags first (auto-skips manual review if triggered); otherwise **uses `ask_user`** to pause: approve / feedback+re-plan / abort; on approval all planned fixes proceed to implementation; abort → delete feature branch, no changes
5. **`w2-fixer`** (Sub-Agent 3) **+ `w2-validator`** (Sub-Agent 4) **loop** — fixer applies all planned fixes; CRITICAL first; property-backed preferred; uses the `Declared in:` file path from CONTEXT_MAP per fix; validator runs `dependency:tree` → `compile` → `mvn test` → smoke check; on failure captures FAILURE_CONTEXT and loops back — **never reverts anything**; on exceeding `retry_limits.build_failure_max` build failures offers **partial-fix commit** or full escalation (**Build Failure counter**)
6. **`w2-verifier`** (Sub-Agent 5) — Jira cross-check via **`jira_ticket_manager.py get`** → CVE manifest validation → regression check → test coverage; outputs VERIFICATION_RESULT (passed/issues_found); on issues **resets `BUILD_FAILURE_ATTEMPTS` to 0** then loops back to fixer+validator (**Verify Fix counter**, max per `retry_limits.verify_fix_max`)
7. **Verification loop** — issues found → re-runs fixer+validator+verifier; exceeding max → escalate
8. **Human reviews implementation** — **uses `ask_user`** to pause: approve / request fixes / abort; on approval, stages **only modified `pom.xml` files** via `git diff --name-only` (NOT `git add -u`) **and commits them** (per updated user preference — commit now happens here, before Step 9, rather than being left to the user); fix requests pass review comments directly as FAILURE_CONTEXT and loop back through fixer + validator + verifier (**Review Fix counter**, max per `retry_limits.review_fix_max`)
9. **`w2-reporter`** (Sub-Agent 6) — pushes branch, creates GitHub PR with four mandatory elements: (1) linked to Jira ticket, (2) summary of changes, (3) test results attached, (4) verified & ready for merge; posts report as Jira comment via **`jira_ticket_manager.py comment`**; transitions ticket via **`jira_ticket_manager.py transition`** (Done / In Review); writes a **workflow summary file** (`fix-reports/SECURITY_FIX_<ticket>_<timestamp>.md`) capturing: steps followed (timeline table), decisions made (plan approval + human review gates), issues encountered (build failure errors, verifier issues), and retry counters

Fix strategy rules: property-backed → update `<properties>` only (preferred); inline → update `<version>` directly; BOM-managed → skip, noted in report.

### Dependabot Schedule

Configured in `.github/dependabot.yml` — weekly on Mondays at 09:00 IST, maven ecosystem, max 5 open PRs.
