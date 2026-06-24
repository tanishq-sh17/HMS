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

**Jira API** — `jira_ticket_manager.py` (`.claude/scripts/jira_ticket_manager.py`) requires:
1. `pip install requests python-dotenv` (one-time setup)
2. Fill in `.env` at repo root:
   ```
   JIRA_BASE_URL=https://tanishqshrivas.atlassian.net   # also accepted as JIRA_URL
   JIRA_EMAIL=<your-atlassian-account-email>
   JIRA_API_TOKEN=<your-atlassian-api-token>
   ```
3. Verify auth: `python .claude/scripts/jira_ticket_manager.py search --jql "project=HMS AND labels=GHAS"`

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
    w1-sorter.md
    w1-jira-manager.md
    vuln-resolver-orchestrator.md
    w2-context-builder.md
    w2-planner.md                     ← change plan + proposed diff (replaces w2-rca)
    w2-fixer.md
    w2-validator.md
    w2-github-reviewer.md             ← analyses reviewer comments, produces suggested fixes
    w2-verifier.md                    ← comprehensive verification before PR creation
    w2-reporter.md
  scripts/                            ← scripts invoked by agents
    fetch_alerts.sh                   ← active: gh CLI → timestamped CSV (all alert types)
    fetch_dependabot_alerts.py        ← legacy: Dependabot only, Excel output

.github/agents/                       ← mirror of .claude/agents/ (kept in sync manually)
```

**Two-folder setup:** `.github/agents/` is loaded by GitHub Copilot CLI; `.claude/agents/` is the identical mirror loaded by Claude Code. Keep them in sync when modifying agent definitions.

### Workflow 1 — Alert Ingestion

Steps run in order; any failure stops the workflow.

1. **`w1-fetcher`** — runs `fetch_alerts.sh` via Git Bash using `gh` CLI (no `.env` token needed — run `gh auth login` once); fetches Dependabot, Code Scanning, and Secret Scanning alerts; writes a timestamped CSV to the repo root
2. **`w1-sorter`** — reads the CSV, groups alerts by service into a dict; does NOT re-sort (already sorted by the script)
3. **`w1-jira-manager`** — for each service, JQL-searches Jira (`project=HMS AND labels=GHAS AND labels=<SERVICE>`); skips if open ticket found, otherwise creates **one consolidated ticket per service** (all CVEs combined); updates CSV with Jira key + status

**Jira ticket title format:** `Address GHAS vulnerabilities for <SERVICE_NAME> [Critical-<N>, High-<N>, Medium-<N>, Low-<N>]`

**CSV columns (0-indexed):** `service` | `type` | `ghsa_id` | `cve_id` | `title` | `severity` | `created` | `due` | `url` | `Application` | `nonCompliant` | `ageDays` | **`jira_key`** | **`jira_status`**

### Workflow 2 — Vulnerability Resolver

Only input needed: **Jira ticket ID** (e.g. `HMS-16`); everything else is fixed config. Four retry counters with human escalation (max 3 attempts each).

1. **`w2-context-builder`** — fetches open alerts and `pom.xml`; reads latest CSV for context; classifies each dependency as inline / property-backed / BOM-managed; audits sibling group consistency for `jjwt-*`, `log4j-*`, `jackson-*`
2. **Feature branch created** — before any file is modified (named `{jira_id}-GHAS-{primary_package}[-and-N-more]`)
3. **`w2-planner`** — scans source files to find which vulnerable packages are actually imported; generates CHANGE_PLAN with proposed `pom.xml` diff and breakage risk; supports re-planning when user gives feedback (**Plan Revision counter**, max 3)
4. **User approval gate** — per-fix approve/skip/abort; auto-approval rules in config can bypass for CRITICAL or MINOR
5. **`w2-fixer`** — applies fixes CRITICAL first; property-backed preferred; updates all siblings when fixing one; supports re-run mode with FAILURE_CONTEXT to retry only failing fixes (**Build Failure counter**, max 3)
6. **`w2-validator`** — `dependency:tree` → `compile` → `mvn test` → smoke check; on failure captures FAILURE_CONTEXT and reports to orchestrator — **never reverts anything**
7. **Human reviews implementation** — approve / request fixes / abort; fix requests invoke `w2-github-reviewer` then loop back through fixer + validator (**Review Fix counter**, max 3)
8. **`w2-verifier`** — Jira cross-check → CVE manifest validation → regression check → test coverage; outputs VERIFICATION_RESULT (passed/issues_found) (**Verify Fix counter**, max 3)
9. **`w2-reporter`** — pushes branch, creates GitHub PR, compiles full report, posts as Jira comment, transitions ticket to Done / In Review

Fix strategy rules: property-backed → update `<properties>` only (preferred); inline → update `<version>` directly; BOM-managed → skip, noted in report.

### Dependabot Schedule

Configured in `.github/dependabot.yml` — weekly on Mondays at 09:00 IST, maven ecosystem, max 5 open PRs.
