"""Run with PYTHONPATH=stitch:stitchi python -m unittest discover -s tests."""

import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from stitch._project import Project, ProjectConfig
from stitch.commands.fuzz import _do_fuzz_full
from stitchi.inference.looper import compile_harness, run_campaign


def executable(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


class FuzzStartupTests(unittest.TestCase):
    def test_failed_conversion_or_build_stops_compilation(self):
        for failed_stage in ("inference", "build"):
            with self.subTest(stage=failed_stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                executable(root / "stitchi", (
                    'echo "$1" >> "$STITCH_TEST_CALLS"\n'
                    f'[ "$1" != "{failed_stage}" ] || exit 23\n'
                ))
                calls = root / "calls"
                with patch.dict(os.environ, {"PATH": tmp, "STITCH_TEST_CALLS": str(calls)}):
                    with self.assertRaises(subprocess.CalledProcessError) as failure:
                        compile_harness(root)
                self.assertEqual(failure.exception.returncode, 23)
                expected = ["inference"] if failed_stage == "inference" else ["inference", "build"]
                self.assertEqual(calls.read_text().splitlines(), expected)

    def test_failed_warmup_never_starts_background_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaign = Path(tmp)
            workdir = campaign / "revisions" / "0"
            workdir.mkdir(parents=True)
            executable(workdir / "fuzzer", "exit 23\n")
            with patch("stitchi.inference.looper.compile_harness"), patch(
                "stitchi.inference.looper.RestartableProcess"
            ) as worker:
                with self.assertRaises(subprocess.CalledProcessError) as failure:
                    run_campaign(campaign, "info.json", revise=True)
            self.assertEqual(failure.exception.returncode, 23)
            worker.assert_not_called()

    def test_failed_looper_runs_once_and_preserves_exit_status(self):
        for use_stdbuf in (True, False):
            with self.subTest(stdbuf=use_stdbuf), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project = Project(root)
                project.create()
                project.set_config(ProjectConfig())
                (root / "config").mkdir()
                (root / "config" / "info.json").write_text("{}")
                harness = project.get_harness("harn1")
                harness.create()
                harness.harness_config().write_text("{}")
                args = SimpleNamespace(
                    model=None, name="harn1", no_asan=False, hard_rss_limit_mb=1024,
                    reset=False, no_corpus_copy=False, max_revisions=30,
                )
                process = Mock()
                process.poll.return_value = 23
                with patch.object(project, "build_docker", return_value="test"), patch.object(
                    project, "invoke", return_value=process
                ) as invoke, patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
                    self.assertEqual(_do_fuzz_full(args, project), 23)
                command = invoke.call_args.kwargs["cmd"]
                command = command.replace("/fuzz/workspace", tmp).replace("/fuzz/persist", tmp + "/persist")
                bindir = root / "bin"
                bindir.mkdir()
                (bindir / "mkdir").symlink_to("/bin/mkdir")
                executable(bindir / "stitchi", 'echo called >> "$STITCH_TEST_CALLS"\nexit 23\n')
                if use_stdbuf:
                    executable(bindir / "stdbuf", 'shift 2\nexec "$@"\n')
                calls = root / "calls"
                env = {**os.environ, "PATH": str(bindir), "STITCH_TEST_CALLS": str(calls)}
                result = subprocess.run(["/bin/bash", "-c", command], env=env)
                self.assertEqual(result.returncode, 23)
                self.assertEqual(calls.read_text().splitlines(), ["called"])


if __name__ == "__main__":
    unittest.main()
