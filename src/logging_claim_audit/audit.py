"""Core audit logic: docs logging claims vs Python source evidence."""

import ast
import fnmatch
import json
import re
from pathlib import Path

VERSION = "0.1.0"

# Longest first so a longer phrase wins when both match one sentence;
# families collapse matches anyway, order only affects reported phrases.
FILELOG_PHRASES = [
    "logs are written to a file",
    "writes logs to a file",
    "write logs to a file",
    "logs to a file",
    "log files",
    "log file",
]

STRUCTURED_PHRASES = [
    "structured logging",
    "logs in json format",
    "json formatted logs",
    "structured logs",
    "logs as json",
    "json logs",
]

NOLOG_PHRASES = [
    "does not log anything",
    "never logs anything",
    "does not log",
    "without logging",
    "never logs",
    "no logging",
    "no logs",
]

FILELOG = "filelog"
STRUCTURED = "structured"
NOLOG = "nolog"

PHRASE_FAMILIES = (
    [(p, FILELOG) for p in FILELOG_PHRASES]
    + [(p, STRUCTURED) for p in STRUCTURED_PHRASES]
    + [(p, NOLOG) for p in NOLOG_PHRASES]
)

NEGATIONS = {"no", "not", "never", "without"}
FILE_HANDLERS = {
    "FileHandler",
    "RotatingFileHandler",
    "TimedRotatingFileHandler",
    "WatchedFileHandler",
}
LOG_LEVELS = {
    "debug",
    "info",
    "warning",
    "warn",
    "error",
    "exception",
    "critical",
    "log",
}
STRUCTURED_LIBS = {"structlog", "pythonjsonlogger"}
ANYLOG_LIBS = {"logging", "structlog", "loguru"}
EXCLUDE_DIRS = {"tests", "fixtures", "docs", "examples"}
TEST_DIRS = {"tests", "fixtures"}


def normalize(text):
    text = text.lower().replace("n't", " not")
    text = text.replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def strip_fenced(text):
    lines = text.splitlines()
    out = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def split_sentences(text):
    parts = re.split(r"[.!?\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def extract_claims(root, ignore):
    """Return {family: [phrase, ...]} found in root README + docs/**/*.md."""
    doc_files = []
    readme = root / "README.md"
    if readme.is_file() and not readme.is_symlink():
        doc_files.append(readme)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in sorted(docs_dir.rglob("*.md")):
            if not path.is_symlink():
                doc_files.append(path)
    found = {}
    for path in doc_files:
        rel = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in ignore):
            continue
        text = strip_fenced(path.read_text(encoding="utf-8", errors="replace"))
        for sentence in split_sentences(normalize(text)):
            tokens = set(sentence.split(" "))
            negated = bool(tokens & NEGATIONS)
            for phrase, family in PHRASE_FAMILIES:
                if family in (FILELOG, STRUCTURED) and negated:
                    continue
                pattern = r"\b" + re.escape(phrase) + r"\b"
                match = re.search(pattern, sentence)
                if not match:
                    continue
                if family == NOLOG:
                    # File-scoped denials ("never logs to a file") deny a
                    # file sink, not record keeping itself: no claim.
                    rest = sentence[match.end():]
                    if rest.startswith(" to a file") or rest.startswith(" to files"):
                        continue
                found.setdefault(family, [])
                if phrase not in found[family]:
                    found[family].append(phrase)
    return found


def dotted(node):
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    return None


def local_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        if isinstance(node, ast.arguments):
            for arg in list(node.posonlyargs) + list(node.args) + list(node.kwonlyargs):
                names.add(arg.arg)
            if node.vararg:
                names.add(node.vararg.arg)
            if node.kwarg:
                names.add(node.kwarg.arg)
    return names


def collect_evidence(path, rel):
    """Return (file_items, structured_items, anylog_items) for one .py file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return [], [], []
    shadowed = local_names(tree)
    file_items = []
    structured_items = []
    anylog_items = []

    def add(bucket, kind, lineno, detail):
        item = "%s:%s %s (%s)" % (rel, lineno, kind, detail)
        if item not in bucket:
            bucket.append(item)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in ANYLOG_LIBS:
                    add(anylog_items, "import", node.lineno, alias.name)
                if top in STRUCTURED_LIBS:
                    add(structured_items, "import", node.lineno, alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in ANYLOG_LIBS:
                    add(anylog_items, "import", node.lineno, node.module)
                if top in STRUCTURED_LIBS:
                    add(structured_items, "import", node.lineno, node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
                if name in FILE_HANDLERS and name not in shadowed:
                    add(file_items, "call", node.lineno, name)
                    add(anylog_items, "call", node.lineno, name)
                elif name == "JsonFormatter" and name not in shadowed:
                    add(structured_items, "call", node.lineno, name)
                    add(anylog_items, "call", node.lineno, name)
                elif name == "getLogger" and name not in shadowed:
                    add(anylog_items, "call", node.lineno, name)
                elif name.lower() == "basicconfig":
                    for kw in node.keywords:
                        if kw.arg == "filename" and not (
                            isinstance(kw.value, ast.Constant) and kw.value.value is None
                        ):
                            add(file_items, "call", node.lineno, "basicConfig(filename=...)")
                            add(anylog_items, "call", node.lineno, "basicConfig(filename=...)")
            elif isinstance(func, ast.Attribute):
                attr = func.attr
                recv = dotted(func.value) or ""
                recv_lower = recv.lower()
                if attr in FILE_HANDLERS:
                    add(file_items, "call", node.lineno, attr)
                    add(anylog_items, "call", node.lineno, attr)
                elif attr == "JsonFormatter":
                    add(structured_items, "call", node.lineno, attr)
                    add(anylog_items, "call", node.lineno, attr)
                elif attr == "getLogger":
                    add(anylog_items, "call", node.lineno, attr)
                elif attr == "basicConfig":
                    for kw in node.keywords:
                        if kw.arg == "filename" and not (
                            isinstance(kw.value, ast.Constant) and kw.value.value is None
                        ):
                            add(file_items, "call", node.lineno, "basicConfig(filename=...)")
                            add(anylog_items, "call", node.lineno, "basicConfig(filename=...)")
                elif attr == "add" and "log" in recv_lower:
                    for arg in node.args[:1]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if arg.value not in ("sys.stdout", "sys.stderr"):
                                add(file_items, "call", node.lineno, "logger.add(file)")
                                add(anylog_items, "call", node.lineno, "logger.add(file)")
                    for kw in node.keywords:
                        if kw.arg == "sink" and isinstance(kw.value, ast.Constant) \
                                and isinstance(kw.value.value, str):
                            add(file_items, "call", node.lineno, "logger.add(sink=file)")
                            add(anylog_items, "call", node.lineno, "logger.add(sink=file)")
                        if kw.arg == "serialize" and isinstance(kw.value, ast.Constant) \
                                and kw.value.value is True:
                            add(structured_items, "call", node.lineno, "logger.add(serialize=True)")
                            add(anylog_items, "call", node.lineno, "logger.add(serialize=True)")
                elif attr in LOG_LEVELS and "log" in recv_lower:
                    add(anylog_items, "call", node.lineno, attr)
                    for arg in node.args:
                        if isinstance(arg, ast.Call):
                            arg_func = arg.func
                            if (isinstance(arg_func, ast.Name) and arg_func.id == "dumps") or (
                                isinstance(arg_func, ast.Attribute) and arg_func.attr == "dumps"
                            ):
                                add(structured_items, "call", node.lineno, "logging(json.dumps(...))")
                                add(anylog_items, "call", node.lineno, "logging(json.dumps(...))")
    return file_items, structured_items, anylog_items


def classify(path, root):
    rel = path.relative_to(root)
    parts = set(rel.parts[:-1])
    if parts & TEST_DIRS or path.name.startswith("test_") or path.name.endswith("_test.py"):
        return "test"
    if parts & EXCLUDE_DIRS:
        return "skip"
    return "prod"


def audit(root, ignore=()):
    ignore = list(ignore or [])
    claims = extract_claims(root, ignore)
    prod_files = []
    test_files = []
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in ignore):
            continue
        kind = classify(path, root)
        if kind == "prod":
            prod_files.append(path)
        elif kind == "test":
            test_files.append(path)
    prod_file_ev, prod_struct_ev, prod_any_ev = [], [], []
    test_file_ev, test_struct_ev, test_any_ev = [], [], []
    for path in prod_files:
        rel = path.relative_to(root).as_posix()
        f, s, a = collect_evidence(path, rel)
        prod_file_ev += f
        prod_struct_ev += s
        prod_any_ev += a
    for path in test_files:
        rel = path.relative_to(root).as_posix()
        f, s, a = collect_evidence(path, rel)
        test_file_ev += f
        test_struct_ev += s
        test_any_ev += a

    findings = []
    verified = []

    def check(family, prod_ev, test_ev, high_rule, med_rule):
        if family not in claims:
            return
        if not prod_files:
            findings.append({
                "rule": high_rule, "severity": "HIGH", "family": family,
                "evidence": ["no-source"],
            })
        elif prod_ev:
            verified.append({"family": family, "phrases": claims[family]})
        elif test_ev:
            findings.append({
                "rule": med_rule, "severity": "MEDIUM", "family": family,
                "evidence": sorted(test_ev),
            })
        else:
            findings.append({
                "rule": high_rule, "severity": "HIGH", "family": family,
                "evidence": ["no evidence"],
            })

    check(FILELOG, prod_file_ev, test_file_ev,
          "documented-file-log-claim-false",
          "documented-file-log-claim-false-test-only")
    check(STRUCTURED, prod_struct_ev, test_struct_ev,
          "documented-structured-log-claim-false",
          "documented-structured-log-claim-false-test-only")
    # NOLOG is inverted: production evidence FALSIFIES the claim.
    if NOLOG in claims:
        if not prod_files:
            findings.append({
                "rule": "documented-no-log-claim-false", "severity": "HIGH",
                "family": NOLOG, "evidence": ["no-source"],
            })
        elif prod_any_ev:
            findings.append({
                "rule": "documented-no-log-claim-false", "severity": "HIGH",
                "family": NOLOG, "evidence": sorted(prod_any_ev),
            })
        elif test_any_ev:
            findings.append({
                "rule": "documented-no-log-claim-false-test-only", "severity": "MEDIUM",
                "family": NOLOG, "evidence": sorted(test_any_ev),
            })
        else:
            verified.append({"family": NOLOG, "phrases": claims[NOLOG]})
    status = "PASS" if not findings else "FAIL"
    return {
        "status": status,
        "findings": findings,
        "verified_claims": verified,
        "claims": {k: v for k, v in claims.items()},
    }


def result_json(result):
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def format_human(result):
    lines = ["%s" % result["status"]]
    for f in result["findings"]:
        lines.append("%s %s (%s)" % (f["severity"], f["rule"], f["family"]))
        for item in f["evidence"]:
            lines.append("  evidence: %s" % item)
    for claim in result["verified_claims"]:
        lines.append("verified: %s" % claim["family"])
    return "\n".join(lines) + "\n"
