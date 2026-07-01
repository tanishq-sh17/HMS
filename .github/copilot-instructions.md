# Copilot Instructions — Hospital Management System (HMS)

## Build & Test Commands

```bash
# Compile only — triggers MapStruct code generation into target/generated-sources/annotations/
mvn compile

# Run all tests
mvn test

# Run a single test class
mvn test -Dtest=AppointmentServiceTest

# Run a single test method
mvn test -Dtest=AppointmentServiceTest#bookAppointment_Success

# Build a runnable JAR (preferred over spring-boot:run for clean start/stop)
mvn package -DskipTests
java -jar target/hospital-management-system-1.0.0.jar

# Run the application in-place (BUILD FAILURE on Ctrl+C is normal)
mvn spring-boot:run

# Clean and recompile — required when MapStruct mappers behave unexpectedly
mvn clean compile
```

**Prerequisites:** MySQL 8 must be running on `localhost:3306`. Update `src/main/resources/application.yml` if your MySQL root password differs from `root`. The database `hms_db` is auto-created on first startup.

## URLs (when running)

| Resource | URL |
|---|---|
| Swagger UI | `http://localhost:8080/api/v1/swagger-ui/index.html` |
| OpenAPI JSON | `http://localhost:8080/api/v1/api-docs` |
| Actuator health | `http://localhost:8080/api/v1/actuator/health` |

> ⚠️ `spring-boot-starter-actuator` is **not** declared in `pom.xml`. The actuator URL above will return 404 unless you add it. The `w2-validator` smoke check falls back to the Swagger UI (HTTP 200) as a workaround.

All non-auth endpoints require `Authorization: Bearer <token>`. Get a token via `POST /api/v1/auth/login`.

---

## Architecture

Spring Boot 3.2.3 / Java 17 REST API with a **feature-based package layout** under `com.hms`. Each feature module (`appointment`, `auth`, `billing`, `doctor`, `medicalrecord`, `patient`, `prescription`) is self-contained with these layers:

```
<feature>/
  controller/     # REST controller — delegates to service, no logic
  dto/            # Request/Response POJOs — never expose entities directly
  entity/         # JPA entity (extends BaseEntity)
  mapper/         # MapStruct interface (Spring-managed bean)
  repository/     # Spring Data JPA interface
  service/        # Interface
  service/impl/   # Implementation — all business logic lives here
  validator/      # Domain-specific validation helpers (optional)
```

Cross-cutting concerns live in dedicated packages:
- `com.hms.common` — `BaseEntity`, `ApiResponse<T>`, shared enums
- `com.hms.config` — `SecurityConfig`, `SwaggerConfig`, `AuditConfig`
- `com.hms.exception` — custom exceptions + `GlobalExceptionHandler`
- `com.hms.security` — JWT filter, token provider, `UserPrincipal`

**Authentication flow**: JWT is verified in `JwtAuthenticationFilter` → `JwtTokenProvider` (JJWT 0.12.x API) → `CustomUserDetailsService` loads `UserPrincipal` → sets `SecurityContext`. Roles are stored as `ROLE_<ENUM_NAME>` (e.g. `ROLE_ADMIN`). `@PreAuthorize("hasRole('ADMIN')")` matches against the `ROLE_` prefix automatically.

**Domain relationships**: `Appointment` links `Patient` ↔ `Doctor`. `Prescription` and `MedicalRecord` also reference both. `Billing` is standalone (created per patient visit).

**Appointment conflict detection**: `AppointmentRepository` has two named queries — `findConflictingAppointment` and `findConflictingAppointmentExcluding` — that check for overlapping doctor time slots while excluding `CANCELLED` and `NO_SHOW` statuses. The `Excluding` variant is used during reschedule so the appointment being moved doesn't conflict with itself.

**Prescription aggregate**: `Prescription.items` is `@OneToMany(cascade = ALL, orphanRemoval = true)`. Removing an item from the in-memory list and saving the parent will physically DELETE that row — always manage the collection through the parent entity.

**Soft delete**: `Patient` uses an `active` boolean (`deactivatePatient()` sets it `false`, no SQL DELETE). `Doctor` uses `available` for the same pattern. ⚠️ `UserPrincipal.isEnabled()` always returns `true`, so a deactivated `User.active = false` record can still authenticate — this is a known gap.

**Business key generation**: `generatePatientCode()`, `generateAppointmentNumber()`, and `generateBillNumber()` all use `repository.count() + 1` at the application layer. This is not concurrency-safe; concurrent inserts will surface as DB unique-constraint violations. The `AtomicLong` fields that exist in service impls are unused.

**JWT does not embed roles**: The token payload only stores the `subject` (username). Every authenticated request triggers a `UserRepository` query via `CustomUserDetailsService` to reload roles. There is no `/auth/refresh` endpoint despite `app.jwt.refresh-expiration` being configured.

**`PatientValidator`** (`com.hms.patient.validator`) exists but is never called from `PatientServiceImpl` — it is dead code.

**Schema management**: `ddl-auto: update` — Hibernate manages the schema automatically. No migration tool (Flyway/Liquibase) is configured.

**Jackson configuration**: `default-property-inclusion: non_null` is set globally — null fields are omitted from all JSON responses. `ApiResponse<T>` also carries `@JsonInclude(NON_NULL)`. `write-dates-as-timestamps: false` means `LocalDateTime` fields serialize as ISO-8601 strings.

**Pagination**: List endpoints accept `?page=0&size=10` query params. Controllers construct `PageRequest.of(page, size, Sort.by(...))` and pass it to repository methods returning `Page<T>`. The sort field and direction are hardcoded per endpoint (e.g., appointments by patient sort by `appointmentDate` descending; by doctor ascending).

---

## Key Conventions

### Entities
- All entities extend `BaseEntity`, which auto-populates `createdAt`, `updatedAt`, `createdBy`, `updatedBy` via Spring Data Auditing.
- Every entity uses Lombok (`@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder`).
- Enums are persisted as `EnumType.STRING`.

### Business codes
Each domain object has a human-readable code generated in the service layer:
| Entity | Code format | Example |
|---|---|---|
| Patient | `PAT-YYYYMM-NNNN` | `PAT-202601-0001` |
| Doctor | `DOC-SPEC-YYYY-NNNN` | `DOC-CAR-2026-0001` |
| Appointment | `APT-YYYYMMDD-NNNNN` | `APT-20260615-00001` |
| Prescription | `RX-YYYYMMDD-NNNNN` | similar pattern |

### DTOs & Responses
- Controllers always return `ApiResponse<T>` (from `com.hms.common.dto`). Use the static factory methods: `ApiResponse.success(data)`, `ApiResponse.success(message, data)`, `ApiResponse.error(message)`.
- MapStruct mappers are configured with `defaultComponentModel=spring` (injected as Spring beans). Mapper interfaces live in `<feature>/mapper/`.

### Exceptions
Throw domain-specific exceptions from services; do **not** return error responses directly. The `GlobalExceptionHandler` maps them to HTTP responses:
| Exception | HTTP status |
|---|---|
| `ResourceNotFoundException` | 404 |
| `AppointmentConflictException` | 409 |
| `DoctorUnavailableException` | 422 |
| `BusinessValidationException` | 400 |

### Transactions
- All write service methods are `@Transactional`.
- All read service methods are `@Transactional(readOnly = true)`.

### Security / Roles
Roles: `ADMIN`, `DOCTOR`, `RECEPTIONIST`, `PATIENT`. Roles are stored with the `ROLE_` prefix — use `hasRole('ADMIN')` (not `hasAuthority('ROLE_ADMIN')`) in `@PreAuthorize`.

Endpoint access rules defined in `SecurityConfig`:

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

### Tests
- Unit tests use `@ExtendWith(MockitoExtension.class)` with `@Mock` / `@InjectMocks` — no Spring context.
- Assertions use AssertJ (`assertThat`, `assertThatThrownBy`).
- Test classes mirror the `impl` class under `test/java/com/hms/<feature>/service/`.
- Tests exist for: `appointment`, `auth`, `billing`, `doctor`, `patient`. The `medicalrecord` and `prescription` modules have no tests yet.

### MapStruct + Lombok
All `toEntity()` mapper methods must include `@BeanMapping(builder = @Builder(disableBuilder = true))` to force MapStruct to use setters instead of the builder. Omitting this causes a compile error because Lombok's `@Builder` does not include fields inherited from `BaseEntity`.

---

## GHAS Vulnerability Management

### Prerequisites

**GitHub CLI** — run `gh auth login` once. `fetch_alerts.sh` uses keyring auth — no token file needed.

**Jira API** — All Jira operations use `jira_ticket_manager.py` (pure Python, no MCP). Setup is required once:
1. `pip install requests python-dotenv pyyaml`
2. Create `.env` at repo root:
   ```
   JIRA_BASE_URL=https://tanishqshrivas.atlassian.net
   JIRA_EMAIL=<your-atlassian-account-email>
   JIRA_API_TOKEN=<your-atlassian-api-token>
   ```
3. Verify: `python .github/scripts/jira_ticket_manager.py search --project HMS --labels GHAS`

> ⚠️ The script tries `JIRA_URL` first, then falls back to `JIRA_BASE_URL`. Use `JIRA_BASE_URL` in `.env` to avoid silent failures.

**Python script operations (all Jira ops):**

| Operation | Subcommand | Agents |
|---|---|---|
| Search tickets (JQL) | `jira_ticket_manager.py search --jql "..."` | w1-jira-manager, alert-ingestion-orchestrator |
| Get issue details | `jira_ticket_manager.py get --ticket HMS-XX` | w2-verifier, vuln-resolver-orchestrator |
| Apply transition | `jira_ticket_manager.py transition --ticket --name` | w2-reporter, alert-ingestion-orchestrator |
| Post comment | `jira_ticket_manager.py comment --ticket --body-file` | w2-reporter |
| Create ticket | `jira_ticket_manager.py create --project --service --csv` | w1-jira-manager |
| Update description | `jira_ticket_manager.py update-description --ticket --service --csv` | w1-jira-manager |

### Jira Configuration
| Setting | Value |
|---|---|
| Jira Site URL | `https://tanishqshrivas.atlassian.net` |
| Jira Project Key | `HMS` |

### Multi-Agent Orchestration
A two-workflow, multi-agent system lives in `.github/agents/` for automated Dependabot vulnerability remediation.

```
.github/
  agents/          ← loaded by GitHub Copilot CLI (@agent-name syntax)
    alert-ingestion-orchestrator.md   ← entry point for Workflow 1
    w1-fetcher.md                     ← runs fetch_alerts.sh, produces CSV (one per service)
    w1-sorter.md                      ← DEPRECATED: no longer invoked; service grouping done inline by orchestrator
    w1-jira-manager.md                ← Jira dedup check + ticket creation
    vuln-resolver-orchestrator.md     ← entry point for Workflow 2 (9-step)
    w2-context-builder.md             ← fetches alerts + parses pom.xml
    w2-planner.md                     ← Sub-Agent 2: scans source; builds CHANGE_PLAN with proposed diff (no code written)
    w2-fixer.md                       ← Sub-Agent 3: patches pom.xml (CRITICAL first)
    w2-validator.md                   ← Sub-Agent 4: dep:tree + compile + test + smoke check
    w2-verifier.md                    ← Sub-Agent 5: Jira cross-check (Python get) + CVE manifest + regression + coverage gate
    w2-reporter.md                    ← Sub-Agent 6: creates GitHub PR, posts Jira comment (Python), transitions ticket (Python)
  config/
    ghas-w1-config.yml                ← Workflow 1 (Alert Ingestion) settings
    ghas-w2-config.yml                ← Workflow 2 (Vulnerability Resolver) settings + retry_limits
  scripts/
    fetch_alerts.sh                   ← active: gh CLI → timestamped CSV (all alert types)
    jira_ticket_manager.py            ← Python script — handles ALL Jira operations (search, get, create, comment, transition, update-description)
    validate_config.py                ← validates ghas-w1-config.yml / ghas-w2-config.yml at startup

.claude/
  agents/          ← mirror of .github/agents/, loaded by Claude Code (claude.ai/code)
```

> **Two-folder setup**: `.github/agents/` is loaded by GitHub Copilot CLI; `.claude/agents/` is the identical mirror loaded by Claude Code. Keep them in sync manually when modifying agent definitions.

**Invoke via Copilot Chat:**
```
@alert-ingestion-orchestrator        ← run Workflow 1 (fetch alerts + create Jira tickets)
@vuln-resolver-orchestrator          ← run Workflow 2 (fix vulnerabilities + report)
```

### Workflow 1 — Alert Ingestion
1. `@w1-fetcher` (Sub-Agent 1) runs `fetch_alerts.sh` once **per service** in a multi-service loop via Git Bash using the `gh` CLI — no `.env` required; run `gh auth login` once. Services are loaded from the root-level `services` array in `ghas-w1-config.yml` as `{ name, github_repo }` objects. If `ALERT_COUNT=0` the fetcher emits that count and exits successfully — it does **not** stop — the orchestrator queues those services for ticket-closure in Step 2b.
2. **Orchestrator derives `SERVICE_NAMES` inline** — after all fetchers complete, `$SERVICE_NAMES = $NONZERO_ALERT_SVCS -join ','`. **`@w1-sorter` is no longer invoked as a sub-agent.**
3. `@w1-jira-manager` (Sub-Agent 2, active) — searches Jira via **`jira_ticket_manager.py search`** using JQL filtered by labels, service, status, and optionally `parent_jira`; if an active ticket exists (status in `skip_statuses`), compares CVEs and **updates its description in-place** via `update-description`; if no active ticket (or prior is Done/Testing/QA), creates a fresh consolidated ticket; updates CSV with Jira key + status
4. **Step 2b (zero-alert closure)** — if any service returned `ALERT_COUNT=0`, orchestrator uses **`jira_ticket_manager.py search`** to find open tickets, then **`jira_ticket_manager.py transition --name Done`** to close them.

**Multi-service config** — add one entry per service under the root-level `services` key (not under `environment`):
```yaml
services:
  - name: HMS               # display name used in Jira labels and CSV
    github_repo: HMS        # GitHub repo name (may differ from service name)
  - name: BillingService
    github_repo: billing-svc
```
`environment.service_name` is the single-service fallback used only when `services` is absent.

> **Testing with multiple services in the same repo** — multiple service entries can share the same `github_repo`. Each gets its own fetcher run, CSV, and Jira ticket under a different service name. Alerts will be identical (same GitHub repo), but the full multi-service loop is exercised.

**W1 Sub-agent reference:**

| # | Sub-agent | Status | Jira tooling |
|---|---|---|---|
| 1 | `@w1-fetcher` | Active | — |
| 2 | `@w1-jira-manager` | Active | Python search, get, create, update-description |
| — | `@w1-sorter` | **Deprecated** — not invoked | — |

**Sub-agent invocation pattern:** WF1 orchestrator loads config once in Step 0, then passes specific values to each sub-agent as explicit variables (`<PLACEHOLDER>` syntax).

| Sub-agent | Variables passed by orchestrator |
|---|---|
| `@w1-fetcher` | `CONFIG_PATH`, `SERVICE_NAME` = `$svc.name`, `REPO_NAME` = `$svc.github_repo` (per-service — may differ from `environment.repo_name`), `REPO_ROOT`, `GIT_BASH`, `GH_CMD`, `PYTHON_CMD`, `FETCH_SCRIPT_UNIX`, `CSV_GLOB`, `REPO_OWNER` |
| `@w1-jira-manager` | `CONFIG_PATH`, `CSV_PATH`, `SERVICE_NAMES`, `SKIP_STATUSES`, `PYTHON_CMD`, `JIRA_SCRIPT`, `JIRA_PROJECT`, `BASE_LABEL`, `CSV_GLOB`, `PARENT_JIRA`, `SEARCH_LABELS` |

**Jira ticket title format:** `Address GHAS vulnerabilities for <SERVICE_NAME> [Critical-<N>, High-<N>, Medium-<N>, Low-<N>]`

**CSV columns:** `service` | `type` | `ghsa_id` | `cve_id` | `title` | `severity` | `created` | `due` | `url` | `Application` | `nonCompliant` | `ageDays` | **`jira_key`** | **`jira_status`**

**Jira ticket table columns**: Set `jira.ticket_table_columns` in `ghas-w1-config.yml` to any subset of `[ghsa_id, cve_id, title, severity, created, due, ageDays, nonCompliant, url]`. The `nonCompliant` column renders as **"Compliance Status"** — CSV value `0` → "Compliant" (green), `1` → "Non-Compliant" (red, bold). Raw numbers are never shown.

### Workflow 2 — Vulnerability Resolver
Only input needed: **Jira ticket ID** (e.g. `HMS-23`). All other settings come from `ghas-w2-config.yml`. Steps run in order (9-step flow):

**W2 Sub-agent reference:**

| # | Sub-agent | Step | Jira tooling |
|---|---|---|---|
| 1 | `@w2-context-builder` | Step 1 | — |
| 2 | `@w2-planner` | Step 3 | — |
| 3 | `@w2-fixer` | Step 5a (loop) | — |
| 4 | `@w2-validator` | Step 5b (loop) | — |
| 5 | `@w2-verifier` | Step 6 | Python `get` |
| 6 | `@w2-reporter` | Step 9 | Python `comment`, `transition` |

0. **Config validation + Jira ticket validation** — loads `ghas-w2-config.yml`, validates required fields; validates the Jira ticket ID matches the configured project key; fetches ticket details via **`jira_ticket_manager.py get`** to confirm the ticket belongs to the intended service; all sub-agent variables resolved here once; aborts on failure
1. **`w2-context-builder`** (Sub-Agent 1) — fetches open alerts; discovers **all `pom.xml` files** in the project (excludes `target/`), reads each one; reads latest CSV; classifies each dependency as inline / property-backed / BOM-managed and records which file declares the version (`Declared in:` field in CONTEXT_MAP); audits sibling group consistency across all pom files
2. **Feature branch creation** — aborts if working tree is dirty; creates a `git checkout -b` branch using `branch.naming_single/multi` templates from config before any file is modified
3. **`w2-planner`** (Sub-Agent 2) — scans source files for actual imports; generates `CHANGE_PLAN` with proposed diff and breakage risk per fix; **no code written**
4. **User review loop** (max `retry_limits.plan_revision_max` iterations) — checks `auto_approve_minor`/`auto_approve_critical` flags first (skips manual review if triggered); otherwise **uses `ask_user`** to present `CHANGE_PLAN` for approval, feedback, or abort; on approval **all planned fixes proceed** (no per-fix gate); exceeding max → escalate; abort → delete feature branch, no changes
5. **`w2-fixer`** (Sub-Agent 3) **+ `w2-validator`** (Sub-Agent 4) **loop** (max `retry_limits.build_failure_max` build failures) — fixer uses the `Declared in:` file path from CONTEXT_MAP per fix (supports multi-module projects — edits the correct child pom.xml, not always the root), applies fixes CRITICAL first with property-backed preferred; validator runs `mvn dependency:tree` → `mvn compile` → `mvn test` → smoke check; build failure retries with FAILURE_CONTEXT; on exceeding max offers **partial-fix commit** (commit passing fixes, escalate failing ones) or full escalation; **validator never reverts fixes**
6. **`w2-verifier`** (Sub-Agent 5) — Jira cross-check via **`jira_ticket_manager.py get`**; CVE manifest validation; regression check; test coverage; issues found loop back to fixer+validator (max `retry_limits.verify_fix_max` verify cycles)
7. **Verification loop** (max `retry_limits.verify_fix_max` cycles) — issues found → **resets `BUILD_FAILURE_ATTEMPTS` to 0** then re-runs fixer+validator+verifier; exceeding max → escalate
8. **Human reviews implementation** — shows validation + verification results; **uses `ask_user`** to pause for approval, fix request, or abort; approve → stages **only modified `pom.xml` files** via `git diff --name-only` (NOT `git add -u`), does NOT commit (user handles committing); fix requested → comments passed **directly** as `FAILURE_CONTEXT` to `w2-fixer` (no intermediary agent), then re-runs fixer+validator+verifier (max `retry_limits.review_fix_max` review cycles)
9. **`w2-reporter`** (Sub-Agent 6) — pushes feature branch, creates GitHub PR with 4 mandatory elements: (1) linked to Jira ticket, (2) summary of changes (package name, before→after version, CVEs addressed), (3) test results attached, (4) verified & ready for merge; posts report to Jira via **`jira_ticket_manager.py comment`**; transitions ticket via **`jira_ticket_manager.py transition`** (Done / In Review); writes a **workflow summary file** (`fix-reports/SECURITY_FIX_<ticket>_<timestamp>.md`) capturing: steps followed (timeline table), decisions made (plan approval + human review gates), issues encountered (build failure errors, verifier issues), and retry counters

**Retry escalation messages** (limits set via `retry_limits` in `ghas-w2-config.yml`, default 3):
| Counter | Config key | Trigger | Escalation |
|---|---|---|---|
| Plan revisions | `retry_limits.plan_revision_max` | User feedback on change plan | `"Too many plan revision cycles — escalate to team"` |
| Build failures | `retry_limits.build_failure_max` | Build or unit tests fail | `"Too many build failures — escalate to engineer"` |
| Verify fix cycles | `retry_limits.verify_fix_max` | Verifier finds issues | `"Verification keeps failing — manual code review required"` |
| Review fix cycles | `retry_limits.review_fix_max` | Human requests implementation changes | `"Too many review fix cycles — reassign task"` |

Fix strategy rules enforced by `@w2-fixer`:
- **Property-backed versions** (`${some.version}`) → update `<properties>` block only — one change covers all usages (preferred)
- **Inline versions** → update `<version>` tag directly
- **BOM-managed** (no `<version>` tag) → skip, noted in report
- Sibling groups (`jjwt-*`, `log4j-*`, `jackson-*`) must always share the same version — when fixing one, update all siblings

### Workflow Config — Split by Workflow

Settings are split into two files, each validated at startup via `validate_config.py`:

**`.github/config/ghas-w1-config.yml`** — Workflow 1 (Alert Ingestion)

| Section | Purpose |
|---|---|
| `environment` | Repo owner/name, `repo_root` (absolute path) |
| `jira` | Site URL, project key, labels, `ticket_table_columns`, `ticket_summary_template`, `skip_statuses_for_duplicate_check` |
| `services` | GitHub repos to scan (one entry per service) |
| `csv` | Alert export file settings |
| `scripts` | Relative paths to `jira_ticket_manager.py`, `fetch_alerts.sh`, `validate_config.py` |

**`.github/config/ghas-w2-config.yml`** — Workflow 2 (Vulnerability Resolver)

| Section | Purpose |
|---|---|
| `environment` | Repo owner/name, `repo_root` (absolute path) |
| `jira` | Site URL, project key, labels, `open_status_categories`, `transition_done/in_review` |
| `workflow2` | Build tool, manifest path, smoke check URL, `auto_approve_minor/critical` |
| `branch` | `naming_single` / `naming_multi` templates for feature branch names |
| `dependency_groups` | Sibling version consistency rules (`jjwt-*`, `log4j-*`, `jackson-*`) |
| `retry_limits` | Max attempts per counter: `plan_revision_max`, `build_failure_max`, `verify_fix_max`, `review_fix_max` |
| `scripts` | Relative paths to `jira_ticket_manager.py`, `validate_config.py` |

**Config-driven Jira ticket columns**: Set `jira.ticket_table_columns` in `ghas-w1-config.yml` to any subset of `[ghsa_id, cve_id, title, severity, created, due, ageDays, nonCompliant, url]`. Changes take effect on the next ticket creation — no script edits required. The `nonCompliant` column renders as **"Compliance Status"** in the ticket — CSV value `0` → "Compliant" (green), `1` → "Non-Compliant" (red, bold). Raw numbers are never shown.

### Intentionally Vulnerable Dependencies

This is a GHAS/Dependabot demo project. The following dependencies are declared in `pom.xml` solely to generate Dependabot alerts and are **not used in application code**:

| Dependency | Version | CVEs |
|---|---|---|
| `log4j-core` | 2.14.1 | CVE-2021-44228 (Log4Shell), CVE-2021-45046, CVE-2021-45105, CVE-2021-44832 |
| `commons-collections` | 3.2.1 | CVE-2015-7501, CVE-2015-6420 |
| `jackson-databind` | 2.13.2 | CVE-2020-36518, CVE-2022-42003, CVE-2022-42004 |
| `guava` | 29.0-jre | CVE-2020-8908, CVE-2023-2976 |
| `gson` | 2.8.5 | CVE-2022-25647 |
| `commons-text` | 1.9 | CVE-2022-42889 (Text4Shell — RCE via StringSubstitutor interpolation) |
| `snakeyaml` | 1.30 | CVE-2022-1471 (RCE via unsafe deserialization) |
| `h2` | 1.4.200 | CVE-2021-42392, CVE-2022-23221 (unauthenticated RCE via JNDI) |
| `xstream` | 1.4.17 | CVE-2021-39139 and 17 others (RCE via unsafe type processing) |
| `netty-all` | 4.1.72.Final | CVE-2021-43797 (HTTP request smuggling) |

⚠️ **Do not upgrade these without understanding the GHAS workflow impact** — they exist to drive the multi-agent remediation workflows.

**Known caveats with the newer deps:**
- `snakeyaml 1.30` overrides Spring Boot's BOM-managed safe version. During Workflow 2 validation, `w2-validator` may need to add a `<dependencyManagement>` override if the fix doesn't take effect transitively.
- `h2 1.4.200` is declared without `<scope>test</scope>`, placing it on the runtime classpath. Spring Boot may attempt to auto-configure an H2 datasource — watch for startup conflicts during `w2-validator`'s smoke check.
- `xstream 1.4.17` alone generates ~18 Dependabot alerts due to the large number of individually tracked CVEs.

### Dependabot Schedule
Configured in `.github/dependabot.yml` — weekly on Mondays at 09:00 IST, maven ecosystem, max 5 open PRs.
