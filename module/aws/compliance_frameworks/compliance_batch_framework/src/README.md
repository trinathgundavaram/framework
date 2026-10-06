# CMS Compliance Framework: source

A project-agnostic, filename-driven framework for CMS compliance source files: batch registration,
file intake, validation, promotion to core, manual overrides (reuse, late arrival, correction) and
**closing each batch** after its SLA hold. The extract is produced by a separate process that reads
the batches. PostgreSQL + Python; runs locally or as AWS Glue jobs.

| Start here | |
|---|---|
| Setup, settings, commands, onboarding | [`docs/framework-package.md`](docs/framework-package.md) |
| What each module does | [`docs/module-reference.md`](docs/module-reference.md) |
| Design and decisions (v6) | [`docs/design/cms-compliance-framework-design.md`](docs/design/cms-compliance-framework-design.md) |
| Schema (source of truth) | [`framework/sql/schema.sql`](framework/sql/schema.sql) |

**What lives where (v6)**

| In tables (6 reference / configuration) | Outside tables |
|---|---|
| `ComplianceProject`, `ComplianceSourceSystem`, `ComplianceRunType` (incl. `SLA_Days`, `Carry_Fwd_Ind`), `ComplianceDataSetSourceXwalk` (which project/table/source/run type apply, and when), `ComplianceSourceFileConfig` (file contract, paths, targets, recipients), `ComplianceRuleBinding` (file rules at project / table / run type / source level) | Database connection: `.env` locally, Secrets Manager in AWS · Settings: `--set` job arguments > environment > `.env` · Report period: `run --module BATCH_CREATION --period <NAME>` (`period_sql.py`) · Event vocabulary: `audit.py` |

```bash
pip install .                             # Python 3.9+
export FRAMEWORK_DB_DSN=postgresql://user@host:5432/db
framework test-connection                                                    # the tables already exist
framework list-modules                                                       # every module, its parameters
framework run --module BATCH_CREATION --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH
framework run --module BATCH_CREATION --project PRJA                         # that project's ad-hoc requests only
framework run --module FILE_LOAD --bucket inbound --key prja/in/PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt
framework run --module FILE_LOAD                                              # sweep every configured inbound location
framework close-batches --project PRJA                                        # SLA sweep: closes batches with data
framework close-batch --btch-id <Btch_ID> --closed-by jdoe                    # a person closes a batch without data
```

Every batch-creation and file-loading job is started through `run --module NAME` (modules.py) - one
entry point per concern, the name picks what runs, and everything is scoped to `--project` where that
applies. There is no separate `create-batches` / `process-intake` / `ingest-file` / `ingest-path`
command; `BATCH_CREATION` and `FILE_LOAD` are it.

---

## Framework source and wheel build

This folder is the Python source of the compliance batch framework (the `cms-compliance-framework`
package). The Glue jobs do not read it directly: it is packaged into a wheel,
`../code/wheels/cms_compliance_framework-<version>-py3-none-any.whl`, which the `glue` submodule uploads
to S3 and each Glue run installs.

```
src/
  pyproject.toml          package name, version, dependencies, what goes into the wheel
  framework/              the package (the only thing inside the wheel)
    ingest.py rules.py batches.py closing.py overrides.py audit.py modules.py cli.py ...
    sql/schema.sql        DDL of the metadata tables (created separately; the framework never creates them)
    sql/approvals.sql     manual override templates
  build_wheel.sh          builds the wheel into ../code/wheels
  run_workflow.sh         starts a project's Step Functions workflow by hand
  github-workflow/        build-framework-wheel.yaml (GitHub Actions)
  docs/                   setup and settings, module reference, design, deployer role templates
```

### Changing the framework

1. Edit the files under `framework/`.
2. Bump `version` in `pyproject.toml` and `__version__` in `framework/__init__.py` (e.g. 0.3.10 → 0.3.11),
   so every deployed build is identifiable. The Terraform picks up any wheel name.
3. Rebuild the wheel, with GitHub Actions or locally (below).
4. Deploy the `glue` submodule. The next Glue run installs the new wheel.

A changed `sql/schema.sql` is applied to the database separately: the framework never creates or alters tables.

### Rebuilding the wheel with GitHub Actions

Copy `github-workflow/build-framework-wheel.yaml` to `.github/workflows/` at the root of the repository,
then run **Build Compliance Framework Wheel** (Actions → Run workflow) on your branch:

| Input | Default | |
|---|---|---|
| `src_path` | `module/aws/compliance_frameworks/compliance_batch_framework/src` | folder with `pyproject.toml` |
| `output_path` | `module/aws/compliance_frameworks/compliance_batch_framework/code/wheels` | where the wheel is written |

It builds with Python 3.9 (the Glue runtime), replaces the wheel in `output_path` and commits it
to the same branch; an unchanged source gives a byte-identical wheel and no commit. The branch must
allow pushes from `github-actions[bot]` (branch protection).

### Rebuilding the wheel locally

```bash
./build_wheel.sh                      # -> ../code/wheels/
./build_wheel.sh /some/other/folder   # or any output folder
```

It runs `python -m pip wheel --no-deps .` (set `PYTHON=` to choose the interpreter) and removes the old
wheel first.

### Viewing the code inside a wheel

A wheel is a zip file:

```bash
unzip -l ../code/wheels/cms_compliance_framework-*.whl                     # list the files
unzip -p ../code/wheels/cms_compliance_framework-*.whl framework/ingest.py # print one
```

Always change the code here and rebuild; do not edit a wheel in place (pip checks each file against the
hashes in its `RECORD`).
