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
Roles: `ADMIN`, `DOCTOR`, `RECEPTIONIST`, `PATIENT`. Role-based URL rules are defined in `SecurityConfig`. Public endpoints: `/auth/**`, Swagger paths, `/actuator/**`. Roles are stored with the `ROLE_` prefix — use `hasRole('ADMIN')` (not `hasAuthority('ROLE_ADMIN')`) in `@PreAuthorize`.

### Tests
- Unit tests use `@ExtendWith(MockitoExtension.class)` with `@Mock` / `@InjectMocks` — no Spring context.
- Assertions use AssertJ (`assertThat`, `assertThatThrownBy`).
- Test classes mirror the `impl` class under `test/java/com/hms/<feature>/service/`.
- Tests exist for: `appointment`, `auth`, `billing`, `doctor`, `patient`. The `medicalrecord` and `prescription` modules have no tests yet.

### MapStruct + Lombok
All `toEntity()` mapper methods must include `@BeanMapping(builder = @Builder(disableBuilder = true))` to force MapStruct to use setters instead of the builder. Omitting this causes a compile error because Lombok's `@Builder` does not include fields inherited from `BaseEntity`.

---

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
  agents/          ← loaded by GitHub Copilot CLI (@agent-name syntax)
    alert-ingestion-orchestrator.md   ← entry point for Workflow 1
    w1-fetcher.md                     ← runs fetch_alerts.sh, produces CSV
    w1-sorter.md                      ← groups alerts by service
    w1-jira-manager.md                ← Jira dedup check + ticket creation
    vuln-resolver-orchestrator.md     ← entry point for Workflow 2
    w2-context-builder.md             ← fetches alerts + parses pom.xml
    w2-fixer.md                       ← patches pom.xml (CRITICAL first)
    w2-validator.md                   ← dep:tree + compile + test + smoke check
    w2-reporter.md                    ← produces end-to-end report, posts Jira comment
  scripts/
    fetch_alerts.sh                   ← active: gh CLI → timestamped CSV (all alert types)
    fetch_dependabot_alerts.py        ← legacy: Dependabot only → Excel output

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
1. `@w1-fetcher` runs `fetch_alerts.sh` via Git Bash using the `gh` CLI — no `.env` required; run `gh auth login` once. Fetches Dependabot, Code Scanning, and Secret Scanning alerts; writes a timestamped CSV to the repo root.
2. `@w1-sorter` reads the CSV and groups alerts by service for the Jira manager
3. `@w1-jira-manager` searches Jira by `GHAS` + service label — skips if found, creates one consolidated ticket per service if not; updates CSV with Jira key + status

**CSV columns:** `service` | `type` | `ghsa_id` | `cve_id` | `title` | `severity` | `created` | `due` | `url` | `Application` | `nonCompliant` | `ageDays` | **`jira_key`** | **`jira_status`**

> `fetch_dependabot_alerts.py` (also in `.github/scripts/`) is a legacy script that produces Excel output for Dependabot alerts only — do not use it for Workflow 1.

### Workflow 2 — Vulnerability Resolver
Fix strategy rules enforced by `@w2-fixer`:
- **Property-backed versions** (`${some.version}`) → update `<properties>` block only — one change covers all usages (preferred)
- **Inline versions** → update `<version>` tag directly
- **BOM-managed** (no `<version>` tag) → skip, noted in report
- Sibling groups (`jjwt-*`, `log4j-*`, `jackson-*`) must always share the same version — when fixing one, update all siblings

Validation order in `@w2-validator`: `mvn dependency:tree` → `mvn compile` → `mvn test` → `spring-boot:run` health check. Individual failing fixes are reverted, not the whole file.

`@w2-reporter` produces a full end-to-end report (no PR is raised) covering: alerts scanned, fixes applied, validation results, reverted fixes, and flagged concerns.

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
