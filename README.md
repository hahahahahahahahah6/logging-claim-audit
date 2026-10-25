# logging-claim-audit

Offline, stdlib-only audit of documented record-keeping promises in a
Python project against its Python source. It reads docs and code, runs
nothing, uses no network, and never opens the contents of any
environment file.

A project can promise three kinds of record-keeping behaviour, and each
can drift from the code without any test failing:

- a file-sink promise: records are kept in a file the user can find later;
- a machine-readable record promise: records are emitted in a shape a
  parser can consume directly;
- an absence-of-records promise: the tool keeps nothing at all.

The first two fail quietly when the code never builds the corresponding
mechanism. The third fails quietly in the other direction: a user who was
told nothing is kept discovers records the docs said would not exist.
This tool checks all three directions statically, from prose in the root
README and `docs/**/*.md` against imports and calls in production
Python source.

## Installation

```sh
pip install logging-claim-audit
```

Or from source:

```sh
python3 -m pip install .
```

## Usage

```sh
logging-claim-audit /path/to/project
logging-claim-audit /path/to/project --json
logging-claim-audit /path/to/project --fail-on HIGH
logging-claim-audit /path/to/project --ignore "vendor/*"
```

Exit codes: `0` means pass, `2` means at least one finding at or above
the `--fail-on` threshold (default `MEDIUM`), `1` means a usage error.
`--verbose` prints verified promises. `--json` prints findings and
verified promises as JSON.

## Rules

```
documented-file-log-claim-false (HIGH)
  file-sink promise present, zero file-sink evidence in production
  source and zero file-sink evidence under tests/fixtures only.
documented-file-log-claim-false-test-only (MEDIUM)
  file-sink promise present, zero production file-sink evidence, but
  file-sink evidence under tests/fixtures only.
documented-structured-log-claim-false (HIGH)
  machine-readable record promise present, zero machine-readable
  evidence in production source and zero under tests/fixtures only.
documented-structured-log-claim-false-test-only (MEDIUM)
  machine-readable record promise present, zero production
  machine-readable evidence, but such evidence under tests/fixtures only.
documented-no-log-claim-false (HIGH)
  absence-of-records promise present, but production source contains
  record-keeping evidence (any of the kinds below).
documented-no-log-claim-false-test-only (MEDIUM)
  absence-of-records promise present, zero production record-keeping
  evidence, but such evidence under tests/fixtures only.
```

Claim phrases (exact phrases only, matched on normalised prose with
word boundaries; fenced code blocks are excluded, inline code counts):

```
file-sink family:
  "logs to a file", "writes logs to a file", "write logs to a file",
  "logs are written to a file", "log file", "log files"
machine-readable family:
  "structured logging", "structured logs", "json logs",
  "logs in json format", "logs as json", "json formatted logs"
absence family:
  "no logging", "does not log", "never logs", "no logs",
  "without logging", "does not log anything", "never logs anything"
```

Negation on a sentence suppresses the file-sink and machine-readable
families on that sentence, but not the absence family (whose phrases are
themselves negative wordings). A denial scoped to a file sink only (for
example a sentence saying records are never written to a file) yields no
promise of either kind and is not audited. One finding per family at
most; a tree with no production Python source at all yields one HIGH
finding per promised family; a tree with no promise yields a pass even
if record-keeping calls are everywhere.

Evidence (production source only; `tests/`, `fixtures/`, `docs/`,
`examples/`, and symlinked files are excluded from production evidence):

- File-sink evidence: `FileHandler` family calls
  (`FileHandler`, `RotatingFileHandler`, `TimedRotatingFileHandler`,
  `WatchedFileHandler`), `basicConfig` with a `filename` keyword whose
  value is not `None`, and loguru `logger.add` with a string file sink.
- Machine-readable evidence: `structlog` / `pythonjsonlogger` imports,
  `JsonFormatter` calls, loguru `logger.add(serialize=True)`, and a
  record-keeping call whose argument is a `json.dumps(...)` call.
- Any-record evidence (falsifies an absence promise): all of the above,
  plus any record-keeping library import, any record-keeping call on a
  receiver whose dotted name contains `log`, and `getLogger` calls.
- A bare name that is redefined in the same file (a local function,
  class, or parameter with the same name) suppresses name-based evidence
  for that name in that file; attribute-based evidence is unaffected.

## Differentiation

- silent-claim-audit: output volume (quiet/verbose), never whether
  records go through a record-keeping framework or to a file sink.
- format-claim-audit: data formats read/written, never the shape or
  destination of the tool's own operational records.
- privacy-claim-audit: network/telemetry presence, never local record
  keeping.
- bandit/ruff: code issues, never docs promises.
- logging / structlog / loguru documentation: libraries that provide
  record keeping, never docs-vs-code truth.

## Honest limitations

- Static pattern matching, not semantic judgement: a file-sink call in
  dead code counts as evidence, and an unused record-keeping import
  counts as evidence. Both are intentional over-approximations.
- A variable sink value is unknown and skipped; only string-literal
  sinks count for loguru file sinks.
- `basicConfig(filename=None)` and `serialize=False` are explicitly not
  evidence.
- `dictConfig` contents are not parsed: a file handler named only inside
  a configuration dict literal is missed. This is the main recall gap
  alongside print-based records: `print(json.dumps(...))` is not a
  record-keeping call and is not counted.
- A plain `Formatter` with a format string is not machine-readable
  evidence by itself.
- A bare `.info` on a receiver whose name contains no `log` is not a
  record-keeping call.
- This tool checks record-keeping existence, destination, and shape —
  not whether records are complete, rotated, or actually flushed.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

Requires Python 3.9 or later. Standard library only, no dependencies.
