from pathlib import Path
from typing import Optional, List, Tuple, Dict
from pydantic import BaseModel, Field
import os
import sys
import tempfile
import subprocess
import json

from ._colors import Colors, err

CUSTOM_SECCOMP = (Path(__file__).parent / "seccomp.json").absolute()

DEFAULT_MODEL = "gpt-5"


class BuildError(BaseModel):
    stdout: str
    stderr: str


class ProjectConfig(BaseModel):
    name: str = Field(default="unknown")
    repo: Optional[str] = Field(default=None)
    commit: Optional[str] = Field(default=None)
    local_path: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)

    def get_model(self) -> str:
        return self.model if self.model is not None else DEFAULT_MODEL


def resolve_model(cli_model: Optional[str], config: Optional["ProjectConfig"]) -> str:
    """Resolve model: CLI flag > project config > default."""
    if cli_model is not None:
        return cli_model
    if config is not None and config.model is not None:
        return config.model
    return DEFAULT_MODEL


class Harness:
    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def create(self):
        self.path.mkdir(parents=True, exist_ok=True)

    def harness_config(self) -> Path:
        return self.path / "harness.json"

    def campaign_dir(self) -> Path:
        d = self.path / "campaign"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def control_dir(self) -> Path:
        d = self.campaign_dir() / "control"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def analysis_dir(self) -> Path:
        d = self.path / "analysis"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def coverage_dir(self) -> Path:
        d = self.path / "coverage"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def cost_per_task(self) -> Dict[str, float]:
        harness_file = self.harness_config()
        if not harness_file.exists():
            return {}
        with open(harness_file, "r") as f:
            harness = json.load(f)
        if "usage" not in harness:
            return {}
        from .data.metadata import UsageTracker

        usage = UsageTracker.model_validate(harness["usage"])
        return usage.cost_by_task()

    def cost(self) -> float:
        return sum(self.cost_per_task().values())


class Project:
    def __init__(self, path: Path):
        self.path = path

    def create(self):
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "harnesses").mkdir(parents=True, exist_ok=True)

    def bugs_dir(self) -> Path:
        d = self.path / "bugs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def status_file(self) -> Path:
        sf = self.path / "status.json"
        if not sf.exists():
            sf.write_text("{}")
        return sf

    def exists(self) -> bool:
        return self.path.exists()

    def has_config(self) -> bool:
        return (self.path / "stitch.json").exists()

    def get_config(self) -> ProjectConfig:
        with open(self.path / "stitch.json", "r") as f:
            return ProjectConfig.model_validate_json(f.read())

    def set_config(self, config: ProjectConfig):
        with open(self.path / "stitch.json", "w") as f:
            f.write(config.model_dump_json(indent=2))

    # ------------------------------------------------------------------
    # Docker image tagging
    # ------------------------------------------------------------------

    def _docker_variant_suffix(
        self,
        *,
        build_coverage: bool = False,
        use_asan: bool = False,
        override_commit: str = None,
        override_repo: str = None,
        hard_rss_limit_mb: int = 1024,
    ) -> str:
        import hashlib

        config = self.get_config()
        commit = override_commit or (config.commit or "HEAD")
        repo = override_repo or (config.repo or "")
        key = {
            "project_name": config.name,
            "project_path": str(self.path.absolute()),
            "repo": repo,
            "commit": commit,
            "local_path": config.local_path or "",
            "build_coverage": bool(build_coverage),
            "use_asan": bool(use_asan),
            "hard_rss_limit_mb": int(hard_rss_limit_mb),
        }
        raw = json.dumps(key, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:12]

    def _base_image_tag(self, **kw) -> str:
        suffix = self._docker_variant_suffix(**kw)
        return f"base_{self.get_config().name}:{suffix}"

    def _main_image_tag(self, **kw) -> str:
        suffix = self._docker_variant_suffix(**kw)
        return f"main_{self.get_config().name}:{suffix}"

    def has_build_config(self) -> bool:
        return (self.path / "config").exists()

    def get_harnesses(self) -> List[Harness]:
        harness_path = self.path / "harnesses"
        if not harness_path.exists():
            return []
        return [Harness(x) for x in harness_path.iterdir() if x.is_dir()]

    def get_harness(self, name: str) -> Harness:
        return Harness(self.path / "harnesses" / name)

    def next_harness_name(self) -> str:
        i = 1
        while True:
            name = f"harn{i}"
            if not (self.path / "harnesses" / name).exists():
                return name
            i += 1

    def build_base_image(self, name: str):
        REPO_PATH = Path(__file__).parent.parent.parent.absolute()
        subprocess.run(
            [
                "docker", "build",
                "--platform", "linux/amd64",
                "-t", f"{name}_base",
                "-f", str(REPO_PATH / "stitch2" / "stitch2" / "docker" / f"{name}.Dockerfile"),
                str(REPO_PATH),
            ],
            check=True,
        )

    def base_image(
        self,
        build_coverage: bool = False,
        use_asan: bool = False,
        override_commit: str = None,
        override_repo: str = None,
        hard_rss_limit_mb: int = 1024,
    ) -> Tuple[str, Optional[str]]:
        """Generate the base Dockerfile content.

        Returns (dockerfile_content, build_context) where build_context is
        the host path to use as Docker build context for local-path projects,
        or None for git-based projects (use the default temp dir).
        """
        config = self.get_config()
        repo = override_repo if override_repo is not None else config.repo
        commit = override_commit if override_commit is not None else config.commit
        local_path = config.local_path

        lines = ["FROM stitch_base"]
        build_context = None

        if local_path is not None and override_repo is None:
            lines.append("COPY . /fuzz/src")
            build_context = local_path
        elif repo is not None:
            if commit is not None:
                lines.append(
                    f"RUN git clone {repo} /fuzz/src && cd /fuzz/src && "
                    f"git checkout {commit} && git submodule update --init --remote --recursive"
                )
            else:
                lines.append(
                    f"RUN git clone --depth 1 {repo} /fuzz/src && cd /fuzz/src && "
                    f"git submodule update --init --remote --recursive"
                )
        lines.append("WORKDIR /fuzz/src")
        lines.append("ENV DEBIAN_FRONTEND=noninteractive")
        lines.append("ENV LD_LIBRARY_PATH=/fuzz/install/lib")
        lines.append(f"ENV ASAN_OPTIONS=hard_rss_limit_mb={hard_rss_limit_mb}:detect_leaks=0")
        if build_coverage:
            lines.append("ENV STITCH_BUILD_COVERAGE=1")
        if use_asan:
            lines.append("ENV STITCH_USE_ASAN=1")
        return "\n".join(lines) + "\n", build_context

    def build_docker(
        self,
        build_coverage: bool = False,
        use_asan: bool = False,
        override_commit: str = None,
        override_repo: str = None,
        hard_rss_limit_mb: int = 1024,
        custom_dockerfile: Optional[Path] = None,
    ) -> str:
        self.build_base_image("stitch")
        base_dockerfile, base_context = self.base_image(
            build_coverage, use_asan, override_commit, override_repo, hard_rss_limit_mb,
        )

        tag_kw = dict(
            build_coverage=build_coverage,
            use_asan=use_asan,
            override_commit=override_commit,
            override_repo=override_repo,
            hard_rss_limit_mb=hard_rss_limit_mb,
        )
        base_tag = self._base_image_tag(**tag_kw)
        main_tag = self._main_image_tag(**tag_kw)

        if custom_dockerfile is not None:
            import hashlib
            content = custom_dockerfile.read_text()
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
            main_tag = f"{main_tag.split(':')[0]}:{main_tag.split(':')[1]}_{h}"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            (temp_dir / "base.Dockerfile").write_text(base_dockerfile)
            context_dir = base_context if base_context else str(temp_dir)
            res = subprocess.run(
                [
                    "docker", "build", "--platform", "linux/amd64",
                    "-t", base_tag, "-f", temp_dir / "base.Dockerfile", context_dir,
                ]
            )
            if res.returncode != 0:
                err(f"Base image build failed: {res.stderr}")
                res.check_returncode()

            dockerfile_path = custom_dockerfile if custom_dockerfile else (self.path / "config" / "Dockerfile")
            build_context = dockerfile_path.parent if custom_dockerfile else (self.path / "config").absolute()

            res = subprocess.run(
                [
                    "docker", "build", "--platform", "linux/amd64",
                    "--build-arg", f"BASE_IMAGE={base_tag}",
                    "-t", main_tag,
                    "-f", str(dockerfile_path), str(build_context),
                ]
            )
            if res.returncode != 0:
                err(f"Main image build failed: {res.stderr}")
                res.check_returncode()

        return main_tag

    def external_base_image(self, override_commit: str = None) -> Tuple[str, Optional[str]]:
        """Generate the external (reproducer) base Dockerfile content.

        Returns (dockerfile_content, build_context) where build_context is
        the host path to use for local-path projects, or None for git-based.
        """
        config = self.get_config()
        commit = override_commit or config.commit
        build_context = None

        lines = [
            "FROM hgarrereyn/stitch_repro_base@sha256:3ae94cdb7bf2660f4941dc523fe48cd2555049f6fb7d17577f5efd32a40fdd2c",
            "",
        ]

        if config.local_path is not None:
            lines.append("COPY . /fuzz/src")
            build_context = config.local_path
        else:
            lines += [
                f"RUN git clone {config.repo} /fuzz/src && \\",
                f"    cd /fuzz/src && \\",
                f"    git checkout {commit} && \\",
                f"    git submodule update --init --remote --recursive",
            ]

        lines += [
            "",
            "ENV LD_LIBRARY_PATH=/fuzz/install/lib",
            "ENV ASAN_OPTIONS=hard_rss_limit_mb=1024:detect_leaks=0",
            "",
        ]
        base_image = "\n".join(lines)

        config_dockerfile = (self.path / "config" / "Dockerfile").read_text()
        config_dockerfile = "\n".join(config_dockerfile.split("\n")[2:]).strip()

        base_image += (
            "RUN echo '#!/bin/bash\\nexec clang-17 -fsanitize=address -O0 \"$@\"' > /usr/local/bin/clang_wrapper && \\\n"
            "    chmod +x /usr/local/bin/clang_wrapper && \\\n"
            "    echo '#!/bin/bash\\nexec clang++-17 -fsanitize=address -O0 \"$@\"' > /usr/local/bin/clang_wrapper++ && \\\n"
            "    chmod +x /usr/local/bin/clang_wrapper++\n\n"
        )
        config_dockerfile = config_dockerfile.replace("stitch_libafl_cc", "clang_wrapper")
        config_dockerfile = config_dockerfile.replace("stitch_libafl_cxx", "clang_wrapper++")
        base_image += config_dockerfile
        return base_image, build_context

    def build_external_docker(
        self,
        override_commit: str = None,
        testcase: str = None,
        verbose: bool = False,
        custom_dockerfile: str = None,
    ) -> Tuple[str, str]:
        config = self.get_config()
        ext_context = None
        if custom_dockerfile is None:
            base, ext_context = self.external_base_image(override_commit)
        else:
            base = custom_dockerfile
        tag = f"repro_{config.name}"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            (temp_dir / "base.Dockerfile").write_text(base)
            (temp_dir / "testcase.cpp").write_text(testcase)
            if verbose:
                print(f"[{Colors.GREEN}+{Colors.END}] Building external docker…\n{base}")
            context_dir = ext_context if ext_context else str(temp_dir)
            subprocess.run(
                [
                    "docker", "build", "--platform", "linux/amd64",
                    "-t", tag, "-f", temp_dir / "base.Dockerfile", context_dir,
                ],
                check=True,
            )
        return (base, tag)

    def build_docker_capture_errors(
        self,
        build_coverage: bool = False,
        use_asan: bool = False,
    ) -> Tuple[Optional[BuildError], Optional[str]]:
        base_dockerfile, base_context = self.base_image(build_coverage, use_asan)
        base_tag = self._base_image_tag(build_coverage=build_coverage, use_asan=use_asan)
        main_tag = self._main_image_tag(build_coverage=build_coverage, use_asan=use_asan)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            (temp_dir / "base.Dockerfile").write_text(base_dockerfile)
            context_dir = base_context if base_context else str(temp_dir)
            subprocess.run(
                [
                    "docker", "build", "--platform", "linux/amd64",
                    "-t", base_tag, "-f", temp_dir / "base.Dockerfile", context_dir,
                ],
                check=True,
            )
            res = subprocess.run(
                [
                    "docker", "build", "--platform", "linux/amd64",
                    "--build-arg", f"BASE_IMAGE={base_tag}",
                    "-t", main_tag,
                    "-f", self.path / "config" / "Dockerfile",
                    str((self.path / "config").absolute()),
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            if res.returncode != 0:
                stderr = res.stderr or ""
                if "already exists" not in stderr:
                    return BuildError(stdout=res.stdout, stderr=res.stderr), None

        return None, main_tag

    def invoke(
        self,
        env: List[str] = [],
        mounts: List[Tuple[str, str]] = [],
        cmd: str = "bash",
        check: bool = False,
        image: Optional[str] = None,
        run_as_host_user: bool = True,
        popen: bool = False,
        use_tty: Optional[bool] = None,
        **kwargs,
    ):
        full_cmd = ["docker", "run", "--rm", "--platform", "linux/amd64"]

        effective_tty = sys.stdin.isatty() if use_tty is None else bool(use_tty)
        if effective_tty:
            full_cmd.insert(2, "-it")

        if run_as_host_user:
            uid = getattr(os, "getuid", lambda: None)()
            gid = getattr(os, "getgid", lambda: None)()
            if uid is not None and gid is not None:
                full_cmd += ["--user", f"{uid}:{gid}"]

        for e in env:
            full_cmd += ["-e", f"{e}={os.environ.get(e, '')}"]
        for mount in mounts:
            full_cmd += ["-v", f"{mount[0]}:{mount[1]}"]
        full_cmd += ["--security-opt", f"seccomp={CUSTOM_SECCOMP}"]

        image_tag = image or f"main_{self.get_config().name}"
        full_cmd += [image_tag, "bash", "-c", cmd]

        if popen:
            return subprocess.Popen(full_cmd, **kwargs)
        return subprocess.run(full_cmd, check=check, **kwargs)
