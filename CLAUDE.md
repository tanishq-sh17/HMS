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

All non-auth endpoints require `Authorization: Bearer <token>`. Get a token via `POST /api/v1/auth/login`.

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

**MapStruct + Lombok interaction** — entities use `@Builder`, but Lombok's `@Builder` does not include fields inherited from `BaseEntity`. As a result, all `toEntity()` mapper methods must include `@BeanMapping(builder = @Builder(disableBuilder = true))` to force MapStruct to use setters instead of the builder. Omitting this causes a compile error (`Unknown property "createdAt" in result type XxxBuilder`).

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

**Security roles** — `ADMIN`, `DOCTOR`, `RECEPTIONIST`, `PATIENT`. Public endpoints: `/auth/**`, Swagger paths, `/actuator/**`. Use `hasRole('ADMIN')` (not `hasAuthority('ROLE_ADMIN')`) in `@PreAuthorize`.

## Intentionally vulnerable dependencies

This is a GHAS/Dependabot demo project. The following dependencies are declared in `pom.xml` to generate Dependabot alerts and are **not used in application code**:

| Dependency | Version | Purpose |
|---|---|---|
| `log4j-core` | 2.14.1 | CVE-2021-44228 (Log4Shell) |
| `commons-collections` | 3.2.1 | CVE-2015-7501 (deserialization RCE) |
| `jackson-databind` | 2.13.2 | CVE-2020-36518, CVE-2022-42003/42004 |
| `guava` | 29.0-jre | CVE-2020-8908, CVE-2023-2976 |
| `gson` | 2.8.5 | CVE-2022-25647 |

Do not upgrade these without understanding the GHAS workflow impact.

## Tests

All tests are service-layer unit tests using Mockito (`@ExtendWith(MockitoExtension.class)`) — no `@SpringBootTest`, no H2, no Testcontainers. Collaborators are `@Mock`; the class under test uses `@InjectMocks`. Assertions use AssertJ (`assertThat`, `assertThatThrownBy`). Coverage exists for `patient`, `appointment`, `billing`, `doctor`, and `auth` services only. The `medicalrecord` and `prescription` modules have no tests yet.

## GHAS Vulnerability Management

### Jira Configuration

| Setting | Value |
|---|---|
| Jira Site URL | `https://tanishqshrivas.atlassian.net` |
| Jira Project Key | `HMS` |

### Multi-Agent Orchestration

A two-workflow, multi-agent system lives in `.github/agents/` for automated Dependabot vulnerability remediation.

```
.github/
  agents/
    alert-ingestion-orchestrator.md   ← entry point for Workflow 1
    w1-fetcher.md                     ← runs fetch script, produces Excel
    w1-sorter.md                      ← groups alerts by service
    w1-jira-manager.md                ← Jira dedup check + ticket creation
    vuln-resolver-orchestrator.md     ← entry point for Workflow 2
    w2-context-builder.md             ← fetches alerts + parses pom.xml
    w2-fixer.md                       ← patches pom.xml (CRITICAL first)
    w2-validator.md                   ← mvn compile + test + smoke check
    w2-reporter.md                    ← produces end-to-end report
  scripts/
    fetch_dependabot_alerts.py        ← GitHub REST API → sorted, color-coded Excel
```

### Workflow 1 — Alert Ingestion

1. `w1-fetcher` runs `fetch_dependabot_alerts.py` — fetches, sorts, and exports to Excel (requires `GITHUB_TOKEN` in `.env`)
2. `w1-sorter` reads the Excel and groups alerts by service
3. `w1-jira-manager` searches Jira by `GHAS` + service label — skips if found, creates one consolidated ticket per service if not; updates Excel with Jira key + status

**Excel columns:** Service | Repo | Alert # | Severity | CVE ID | Package | Vulnerable Range | Safe Version | Manifest | Scope | Summary | Alert URL | **Jira Key** | **Jira Status**

### Workflow 2 — Vulnerability Resolver

Fix strategy rules enforced by `w2-fixer`:
- **Property-backed versions** (`${some.version}`) → update `<properties>` block only — one change covers all usages (preferred)
- **Inline versions** → update `<version>` tag directly
- **BOM-managed** (no `<version>` tag) → skip, noted in report
- Sibling groups (`jjwt-*`, `log4j-*`, `jackson-*`) must always share the same version — when fixing one, update all siblings

Validation order in `w2-validator`: `mvn dependency:tree` → `mvn compile` → `mvn test` → `spring-boot:run` health check. Individual failing fixes are reverted, not the whole file.

`w2-reporter` produces a full end-to-end report covering: alerts scanned, fixes applied, validation results, reverted fixes, and flagged concerns.

### Dependabot Schedule

Configured in `.github/dependabot.yml` — weekly on Mondays at 09:00 IST, maven ecosystem, max 5 open PRs.
