"""Tests for logging-claim-audit."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from logging_claim_audit.__main__ import build_parser, main
from logging_claim_audit.audit import (
    VERSION,
    audit,
    collect_evidence,
    extract_claims,
    format_human,
    result_json,
)

FIXTURES = Path(__file__).parent / "fixtures"


def make_tree(files):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp, root


def run_cli(args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(args)
    return code, buf.getvalue()


class ClaimTests(unittest.TestCase):
    def claims_of(self, readme, docs=None):
        files = {"README.md": readme}
        for name, text in (docs or {}).items():
            files["docs/" + name] = text
        tmp, root = make_tree(files)
        with tmp:
            return extract_claims(root, [])

    def test_fenced_code_excluded(self):
        text = "```\nThis tool logs to a file.\n```\nPlain text.\n"
        self.assertEqual(self.claims_of(text), {})

    def test_inline_code_counts(self):
        self.assertIn("filelog", self.claims_of("It `logs to a file` daily.\n"))

    def test_doesnt_log_is_nolog(self):
        self.assertIn("nolog", self.claims_of("It doesn't log anything.\n"))

    def test_never_logs_to_file_no_claim(self):
        self.assertEqual(self.claims_of("It never logs to a file.\n"), {})

    def test_no_log_file_no_claim(self):
        self.assertEqual(self.claims_of("See the no log file note.\n"), {})

    def test_log_in_no_claim(self):
        self.assertEqual(self.claims_of("Users log in with SSO.\n"), {})

    def test_catalog_no_claim(self):
        self.assertEqual(self.claims_of("See the catalog for details.\n"), {})

    def test_bare_logging_no_claim(self):
        self.assertEqual(self.claims_of("# Logging\n\nNotes here.\n"), {})

    def test_log_file_claim(self):
        self.assertIn("filelog", self.claims_of("Check the log file.\n"))

    def test_log_files_claim(self):
        self.assertIn("filelog", self.claims_of("Old log files rotate.\n"))

    def test_json_logs_claim(self):
        self.assertIn("structured", self.claims_of("Emits json logs.\n"))

    def test_without_logging_claim(self):
        self.assertIn("nolog", self.claims_of("Runs without logging.\n"))

    def test_hyphen_normalised(self):
        self.assertIn("filelog", self.claims_of("Check the log-file.\n"))

    def test_docs_subdir_counts(self):
        claims = self.claims_of("Plain.\n", {"guide.md": "It logs to a file.\n"})
        self.assertIn("filelog", claims)

    def test_negation_suppresses_positive(self):
        self.assertEqual(
            self.claims_of("This tool does not logs to a file.\n"), {})

    def test_logs_to_file_claim(self):
        self.assertIn("filelog", self.claims_of("It logs to a file.\n"))

    def test_structured_logging_claim(self):
        self.assertIn("structured", self.claims_of("Uses structured logging.\n"))

    def test_no_logging_claim(self):
        self.assertIn("nolog", self.claims_of("Has no logging.\n"))

    def test_never_logs_claim(self):
        self.assertIn("nolog", self.claims_of("It never logs.\n"))

    def test_no_logs_claim(self):
        self.assertIn("nolog", self.claims_of("There are no logs.\n"))

    def test_logs_as_json_claim(self):
        self.assertIn("structured", self.claims_of("Emits logs as json.\n"))

    def test_writes_logs_to_file_claim(self):
        self.assertIn("filelog", self.claims_of("It writes logs to a file.\n"))


class EvidenceTests(unittest.TestCase):
    def evidence_of(self, code):
        tmp, root = make_tree({"main.py": code})
        with tmp:
            return collect_evidence(root / "main.py", "main.py")

    def test_filehandler_attribute(self):
        f, _s, a = self.evidence_of("import logging\nh = logging.FileHandler('x')\n")
        self.assertTrue(f)
        self.assertTrue(a)

    def test_rotating_handler(self):
        f, _s, _a = self.evidence_of(
            "from logging.handlers import RotatingFileHandler\n"
            "h = RotatingFileHandler('x')\n")
        self.assertTrue(f)

    def test_shadowed_filehandler_name(self):
        f, _s, _a = self.evidence_of(
            "def FileHandler(x):\n    pass\n\nh = FileHandler('x')\n")
        self.assertFalse(f)

    def test_basicconfig_filename(self):
        f, _s, _a = self.evidence_of(
            "import logging\nlogging.basicConfig(filename='app.log')\n")
        self.assertTrue(f)

    def test_basicconfig_filename_none(self):
        f, _s, _a = self.evidence_of(
            "import logging\nlogging.basicConfig(filename=None)\n")
        self.assertFalse(f)

    def test_loguru_add_file(self):
        f, _s, _a = self.evidence_of("logger.add('app.log')\n")
        self.assertTrue(f)

    def test_loguru_add_stderr_not_file(self):
        f, _s, _a = self.evidence_of(
            "import sys\nlogger.add(sys.stderr)\n")
        self.assertFalse(f)

    def test_loguru_add_variable_sink_not_file(self):
        f, _s, _a = self.evidence_of("logger.add(sink)\n")
        self.assertFalse(f)

    def test_loguru_add_sink_kwarg(self):
        f, _s, _a = self.evidence_of("logger.add(sink='app.log')\n")
        self.assertTrue(f)

    def test_structlog_import(self):
        _f, s, a = self.evidence_of("import structlog\n")
        self.assertTrue(s)
        self.assertTrue(a)

    def test_pythonjsonlogger_import(self):
        _f, s, _a = self.evidence_of("from pythonjsonlogger import jsonlogger\n")
        self.assertTrue(s)

    def test_jsonformatter_call(self):
        _f, s, _a = self.evidence_of("f = JsonFormatter()\n")
        self.assertTrue(s)

    def test_serialize_true(self):
        _f, s, _a = self.evidence_of("logger.add('app.log', serialize=True)\n")
        self.assertTrue(s)

    def test_serialize_false_not(self):
        _f, s, _a = self.evidence_of("logger.add('app.log', serialize=False)\n")
        self.assertFalse(s)

    def test_logging_json_dumps(self):
        _f, s, _a = self.evidence_of(
            "import json, logging\nlogger = logging.getLogger(__name__)\n"
            "logger.info(json.dumps({'a': 1}))\n")
        self.assertTrue(s)

    def test_print_json_dumps_not(self):
        _f, s, _a = self.evidence_of("import json\nprint(json.dumps({'a': 1}))\n")
        self.assertFalse(s)

    def test_import_logging_anylog(self):
        _f, _s, a = self.evidence_of("import logging\n")
        self.assertTrue(a)

    def test_getlogger_anylog(self):
        _f, _s, a = self.evidence_of("import logging\nl = logging.getLogger('x')\n")
        self.assertTrue(a)

    def test_shadowed_getlogger_name(self):
        _f, _s, a = self.evidence_of(
            "def getLogger(x):\n    pass\n\nl = getLogger('x')\n")
        self.assertFalse(a)

    def test_obj_info_not_logging(self):
        _f, _s, a = self.evidence_of("obj.info('hi')\n")
        self.assertFalse(a)

    def test_dictconfig_literal_not(self):
        # The dict literal adds no file/structured evidence; only the
        # logging import itself counts as anylog evidence.
        f, s, a = self.evidence_of(
            "import logging.config\nlogging.config.dictConfig({\n"
            "    'handlers': {'f': {'class': 'FileHandler'}},\n})\n")
        self.assertFalse(f)
        self.assertFalse(s)
        self.assertEqual(a, ["main.py:1 import (logging.config)"])

    def test_dead_code_counts(self):
        f, _s, _a = self.evidence_of(
            "import logging\ndef never_called():\n"
            "    return logging.FileHandler('x')\n")
        self.assertTrue(f)

    def test_plain_code_no_evidence(self):
        f, s, a = self.evidence_of("x = 1\nprint(x)\n")
        self.assertFalse(f)
        self.assertFalse(s)
        self.assertFalse(a)

    def test_logger_info_counts(self):
        _f, _s, a = self.evidence_of("logger.info('hi')\n")
        self.assertTrue(a)

    def test_self_logger_counts(self):
        _f, _s, a = self.evidence_of("self.logger.error('bad')\n")
        self.assertTrue(a)

    def test_json_import_alone_not_structured(self):
        _f, s, _a = self.evidence_of("import json\n")
        self.assertFalse(s)


class FixtureTests(unittest.TestCase):
    def audit_fixture(self, *parts):
        return audit(FIXTURES.joinpath(*parts), [])

    def test_good_filelog_pass(self):
        result = self.audit_fixture("good", "filelog")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            [c["family"] for c in result["verified_claims"]], ["filelog"])

    def test_good_structured_pass(self):
        result = self.audit_fixture("good", "structured")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            [c["family"] for c in result["verified_claims"]], ["structured"])

    def test_good_nolog_pass(self):
        result = self.audit_fixture("good", "nolog")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            [c["family"] for c in result["verified_claims"]], ["nolog"])

    def test_bad_filelog_exactly_one_high(self):
        result = self.audit_fixture("bad", "filelog")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["rule"],
                         "documented-file-log-claim-false")
        self.assertEqual(result["findings"][0]["severity"], "HIGH")

    def test_bad_structured_exactly_one_high(self):
        result = self.audit_fixture("bad", "structured")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["rule"],
                         "documented-structured-log-claim-false")
        self.assertEqual(result["findings"][0]["severity"], "HIGH")

    def test_bad_nolog_exactly_one_high(self):
        result = self.audit_fixture("bad", "nolog")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["rule"],
                         "documented-no-log-claim-false")
        self.assertEqual(result["findings"][0]["severity"], "HIGH")

    def test_testonly_filelog_medium(self):
        result = self.audit_fixture("testonly", "filelog")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["severity"], "MEDIUM")
        self.assertEqual(result["findings"][0]["rule"],
                         "documented-file-log-claim-false-test-only")

    def test_testonly_fail_on_high_exits_zero(self):
        code, _out = run_cli([str(FIXTURES / "testonly" / "filelog"),
                              "--fail-on", "HIGH"])
        self.assertEqual(code, 0)

    def test_bad_fixture_cli_exit_two(self):
        code, _out = run_cli([str(FIXTURES / "bad" / "filelog")])
        self.assertEqual(code, 2)

    def test_good_fixture_cli_exit_zero(self):
        code, _out = run_cli([str(FIXTURES / "good" / "nolog")])
        self.assertEqual(code, 0)

    def test_env_marker_never_in_outputs(self):
        result = self.audit_fixture("bad", "filelog")
        blob = result_json(result) + format_human(result)
        self.assertNotIn("LOGGING_CLAIM_AUDIT_MARKER", blob)

    def test_json_shape(self):
        result = self.audit_fixture("bad", "filelog")
        data = json.loads(result_json(result))
        self.assertIn("findings", data)
        self.assertIn("verified_claims", data)
        self.assertEqual(data["status"], "FAIL")

    def test_no_claim_pass_despite_evidence(self):
        tmp, root = make_tree({
            "README.md": "Plain tool.\n",
            "main.py": "import logging\nh = logging.FileHandler('x')\n",
        })
        with tmp:
            self.assertEqual(audit(root, [])["status"], "PASS")

    def test_no_source_high(self):
        tmp, root = make_tree({"README.md": "It logs to a file.\n"})
        with tmp:
            result = audit(root, [])
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["findings"][0]["severity"], "HIGH")
            self.assertIn("no-source", result["findings"][0]["evidence"])

    def test_ignore_drops_evidence_file(self):
        tmp, root = make_tree({
            "README.md": "It logs to a file.\n",
            "with_handler.py": "import logging\nh = logging.FileHandler('x')\n",
            "plain.py": "x = 1\n",
        })
        with tmp:
            kept = audit(root, [])
            self.assertEqual(kept["status"], "PASS")
            dropped = audit(root, ["with_handler.py"])
            self.assertEqual(dropped["status"], "FAIL")

    def test_multi_family_independent_findings(self):
        tmp, root = make_tree({
            "README.md": "It logs to a file and uses structured logging.\n",
            "main.py": "x = 1\n",
        })
        with tmp:
            result = audit(root, [])
            self.assertEqual(len(result["findings"]), 2)

    def test_version(self):
        self.assertEqual(VERSION, "0.1.0")
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_usage_error_exit_one(self):
        with self.assertRaises(SystemExit) as ctx:
            main([str(FIXTURES / "good" / "nolog"), "--fail-on", "LOW"])
        self.assertEqual(ctx.exception.code, 1)

    def test_bad_path_exit_one(self):
        code, _out = run_cli(["/nonexistent-dir-xyz"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
