from pathlib import Path
from typing import Optional, List, Tuple, Dict
from pydantic import BaseModel, Field
import os
import sys
import tempfile
import subprocess
import json

from ..utils.colors import Colors

CUSTOM_SECCOMP = (Path(__file__).parent.parent / 'seccomp.json').absolute()


class BuildError(BaseModel):
    stdout: str
    stderr: str


class ProjectConfig(BaseModel):
    name: str = Field(default='unknown', description='The name of the project')
    repo: Optional[str] = Field(default=None, description='The url of the project')
    commit: Optional[str] = Field(default=None, description='The commit hash of the project (or most recent commit if not specified)')


class Harness:
    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()
    
    def create(self):
        self.path.mkdir(parents=True, exist_ok=True)

    def harness_config(self) -> Path:
        return self.path / 'harness.json'

    def campaign_dir(self) -> Path:
        campaign_dir = self.path / 'campaign'
        campaign_dir.mkdir(parents=True, exist_ok=True)
        return campaign_dir

    def control_dir(self) -> Path:
        """
        Control directory for out-of-band coordination with long-running runs.

        Example: creating `<campaign>/control/stop` requests a safe stop for
        `agf fuzz` (honored by the inner looper at safe points).
        """
        control_dir = self.campaign_dir() / 'control'
        control_dir.mkdir(parents=True, exist_ok=True)
        return control_dir
    
    def analysis_dir(self) -> Path:
        analysis_dir = self.path / 'analysis'
        analysis_dir.mkdir(parents=True, exist_ok=True)
        return analysis_dir
    
    def coverage_dir(self) -> Path:
        coverage_dir = self.path / 'coverage'
        coverage_dir.mkdir(parents=True, exist_ok=True)
        return coverage_dir
    
    def num_crashes(self) -> int:
        crash_dir = self.output_dir() / 'crashes'
        if not crash_dir.exists():
            return 0
        return len(list(filter(lambda x: x.is_file() and not x.name.startswith('.'), crash_dir.iterdir())))
    
    def num_crash_types(self) -> int:
        bucket_dir = self.output_dir() / 'analysis' / 'buckets'
        if not bucket_dir.exists():
            return 0
        return len(set([f.stem for f in bucket_dir.iterdir()]))

    def cost_per_task(self) -> Dict[str, float]:
        harness_file = self.harness_config()
        if not harness_file.exists():
            return {}
        with open(harness_file, 'r') as f:
            harness = json.load(f)
        if 'usage' not in harness:
            return {}
        # Lazy import to avoid pulling in heavier optional dependencies (and
        # their transitive imports) for commands that don't need usage tracking.
        from ..data.metadata import UsageTracker

        usage = UsageTracker.model_validate(harness['usage'])
        return usage.cost_by_task()
    
    def cost(self) -> float:
        cost_per_task = self.cost_per_task()
        return sum(cost_per_task.values())

class Project:
    def __init__(self, path: Path):
        self.path = path

    def create(self):
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / 'harnesses').mkdir(parents=True, exist_ok=True)


    def bugs_dir(self) -> Path:
        bugs_dir = self.path / 'bugs'
        bugs_dir.mkdir(parents=True, exist_ok=True)
        return bugs_dir

    def status_file(self) -> Path:
        status_file = self.path / 'status.json'
        if not status_file.exists():
            status_file.write_text('{}')
        return status_file

    def exists(self) -> bool:
        return self.path.exists()
    
    def has_config(self) -> bool:
        return (self.path / 'agf.json').exists()
    
    def get_config(self) -> ProjectConfig:
        with open(self.path / 'agf.json', 'r') as f:
            return ProjectConfig.model_validate_json(f.read())

    def set_config(self, config: ProjectConfig):
        with open(self.path / 'agf.json', 'w') as f:
            f.write(config.model_dump_json(indent=2))

    # ------------------------------------------------------------------
    # Docker image tagging helpers
    # ------------------------------------------------------------------
    def _docker_variant_suffix(
        self,
        *,
        build_coverage: bool = False,
        use_asan: bool = False,
        override_commit: str = None,
        hard_rss_limit_mb: int = 1024,
    ) -> str:
        """
        Compute a deterministic, compact suffix that captures the \"shape\" of a
        build so that:
          - Different build configurations (coverage vs non-coverage, ASAN vs
            non-ASAN, commit override, RSS limit) do NOT clobber each other's
            tags.
          - Identical configurations reuse the same tag across runs so Docker
            layer cache remains effective and disk usage stays bounded.

        This does *not* encode the entire Dockerfile contents; if you change
        the project Dockerfile, Docker's content-addressed layers will still
        force a rebuild even under the same tag.
        """
        import hashlib

        config = self.get_config()
        commit = override_commit or (config.commit or 'HEAD')

        # Include the on-disk project path so that two different projects with
        # the same logical name don't accidentally share tags.
        key = {
            'project_name': config.name,
            'project_path': str(self.path.absolute()),
            'repo': config.repo or '',
            'commit': commit,
            'build_coverage': bool(build_coverage),
            'use_asan': bool(use_asan),
            'hard_rss_limit_mb': int(hard_rss_limit_mb),
        }

        raw = json.dumps(key, sort_keys=True).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()[:12]
        return digest

    def _base_image_tag(
        self,
        *,
        build_coverage: bool = False,
        use_asan: bool = False,
        override_commit: str = None,
        hard_rss_limit_mb: int = 1024,
    ) -> str:
        suffix = self._docker_variant_suffix(
            build_coverage=build_coverage,
            use_asan=use_asan,
            override_commit=override_commit,
            hard_rss_limit_mb=hard_rss_limit_mb,
        )
        config = self.get_config()
        return f'base_{config.name}:{suffix}'

    def _main_image_tag(
        self,
        *,
        build_coverage: bool = False,
        use_asan: bool = False,
        override_commit: str = None,
        hard_rss_limit_mb: int = 1024,
    ) -> str:
        suffix = self._docker_variant_suffix(
            build_coverage=build_coverage,
            use_asan=use_asan,
            override_commit=override_commit,
            hard_rss_limit_mb=hard_rss_limit_mb,
        )
        config = self.get_config()
        return f'main_{config.name}:{suffix}'

    def has_build_config(self) -> bool:
        return (self.path / 'config').exists()

    def get_harnesses(self) -> List['Harness']:
        harness_path = self.path / 'harnesses'
        if not harness_path.exists():
            return []
        return [Harness(x) for x in harness_path.iterdir() if x.is_dir()]

    def build_base_image(self, name: str):
        REPO_PATH = (Path(__file__).parent.parent.parent.parent).absolute()
        subprocess.run([
            'docker', 'build',
            '--platform', 'linux/amd64',
            '-t', f'{name}_base',
            '-f', str(REPO_PATH / 'agf' / 'agf' / 'docker' / f'{name}.Dockerfile'),
            str(REPO_PATH)
        ], check=True)

    def base_image(self, build_coverage: bool = False, use_asan: bool = False, override_commit: str = None, hard_rss_limit_mb: int = 1024) -> str:
        """
        Builds the base image for the project.
        """
        config = self.get_config()

        base_image = ''
        base_image += f'FROM stitch_base\n'
        if config.repo is not None:
            if config.commit is not None or override_commit is not None:
                base_image += f'RUN git clone {config.repo} /fuzz/src && cd /fuzz/src && git checkout {config.commit if override_commit is None else override_commit} && git submodule update --init --remote --recursive\n'
            else:
                base_image += f'RUN git clone --depth 1 {config.repo} /fuzz/src && cd /fuzz/src && git submodule update --init --remote --recursive\n'
        base_image += 'WORKDIR /fuzz/src\n'
        base_image += 'ENV DEBIAN_FRONTEND=noninteractive\n'
        base_image += 'ENV LD_LIBRARY_PATH=/fuzz/install/lib\n'
        base_image += f'ENV ASAN_OPTIONS=hard_rss_limit_mb={hard_rss_limit_mb}:detect_leaks=0\n'
        if build_coverage:
            base_image += 'ENV GRAPHFUZZ_BUILD_COVERAGE=1\n'
        if use_asan:
            base_image += 'ENV GRAPHFUZZ_USE_ASAN=1\n'
        return base_image

    def build_docker(self, build_coverage: bool = False, use_asan: bool = False, override_commit: str = None, hard_rss_limit_mb: int = 1024) -> str:
        self.build_base_image('stitch')
        base = self.base_image(build_coverage, use_asan, override_commit, hard_rss_limit_mb)

        base_tag = self._base_image_tag(
            build_coverage=build_coverage,
            use_asan=use_asan,
            override_commit=override_commit,
            hard_rss_limit_mb=hard_rss_limit_mb,
        )
        main_tag = self._main_image_tag(
            build_coverage=build_coverage,
            use_asan=use_asan,
            override_commit=override_commit,
            hard_rss_limit_mb=hard_rss_limit_mb,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            (temp_dir / 'base.Dockerfile').write_text(base)
            res = subprocess.run([
                'docker', 'build',
                '--platform', 'linux/amd64',
                '-t', base_tag,
                '-f', temp_dir / 'base.Dockerfile',
                str(temp_dir)
            ])
            if res.returncode != 0:
                print(f'[{Colors.RED}-{Colors.END}] Base image build failed: {res.stderr}')
                res.check_returncode()

            res = subprocess.run(
                [
                    'docker',
                    'build',
                    '--platform',
                    'linux/amd64',
                    '--build-arg',
                    f'BASE_IMAGE={base_tag}',
                    '-t',
                    main_tag,
                    '-f',
                    self.path / 'config' / 'Dockerfile',
                    str((self.path / 'config').absolute()),
                ],
            )
            if res.returncode != 0:
                print(f'[{Colors.RED}-{Colors.END}] Main image build failed: {res.stderr}')
                res.check_returncode()

        return main_tag

    def external_base_image(self, override_commit: str = None) -> str:
        """Build a fully external base image and return the Dockerfile."""
        config = self.get_config()
        base_image = ''
        base_image += f'# Ubuntu 22.04 with some packages pre-installed\nFROM hgarrereyn/stitch_repro_base@sha256:3ae94cdb7bf2660f4941dc523fe48cd2555049f6fb7d17577f5efd32a40fdd2c\n\n'
        base_image += f'RUN git clone {config.repo} /fuzz/src && \\\n    cd /fuzz/src && \\\n    git checkout {config.commit if override_commit is None else override_commit} && \\\n    git submodule update --init --remote --recursive\n\n'
        base_image += 'ENV LD_LIBRARY_PATH=/fuzz/install/lib\n'
        base_image += f'ENV ASAN_OPTIONS=hard_rss_limit_mb=1024:detect_leaks=0\n\n'

        # Concatenate the config image
        config_dockerfile = (self.path / 'config' / 'Dockerfile').read_text()

        # TODO: clean this up a bit
        # 1. Cut the first two lines off and strip whitespace
        config_dockerfile = config_dockerfile.split('\n')[2:]
        config_dockerfile = '\n'.join(config_dockerfile)
        config_dockerfile = config_dockerfile.strip()

        # Create wrapper scripts that inject -fsanitize=address -O0
        base_image += (
            "RUN echo '#!/bin/bash\\nexec clang-17 -fsanitize=address -O0 \"$@\"' > /usr/local/bin/clang_wrapper && \\\n"
            "    chmod +x /usr/local/bin/clang_wrapper && \\\n"
            "    echo '#!/bin/bash\\nexec clang++-17 -fsanitize=address -O0 \"$@\"' > /usr/local/bin/clang_wrapper++ && \\\n"
            "    chmod +x /usr/local/bin/clang_wrapper++\n\n"
        )

        # Replace gf_libafl_cc with clang_wrapper
        config_dockerfile = config_dockerfile.replace('gf_libafl_cc', 'clang_wrapper')
        # Replace gf_libafl_cxx with clang_wrapper++
        config_dockerfile = config_dockerfile.replace('gf_libafl_cxx', 'clang_wrapper++')

        base_image += config_dockerfile

        return base_image

    def build_external_docker(self, override_commit: str = None, testcase: str = None, verbose: bool = False, custom_dockerfile: str = None) -> Tuple[str, str]:
        """Build a fully external docker reproducer and return the Dockerfile"""
        config = self.get_config()
        # self.build_base_image('stitch_repro')
        if custom_dockerfile is None:
            base = self.external_base_image(override_commit)
        else:
            print(f'[{Colors.GREEN}+{Colors.END}] Using custom Dockerfile: {custom_dockerfile}')
            base = custom_dockerfile
        tag = f'repro_{config.name}'
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            (temp_dir / 'base.Dockerfile').write_text(base)
            (temp_dir / 'testcase.cpp').write_text(testcase)
            if verbose:
                print(f'[{Colors.GREEN}+{Colors.END}] Building external docker...')
                print(f'{base}')
            subprocess.run([
                'docker', 'build',
                '--platform', 'linux/amd64',
                '-t', tag,
                '-f', temp_dir / 'base.Dockerfile',
                str(temp_dir)
            ], check=True)
        return (base, tag)

    def build_docker_capture_errors(self, build_coverage: bool = False, use_asan: bool = False) -> Tuple[Optional[BuildError], Optional[str]]:
        base = self.base_image(build_coverage, use_asan)

        base_tag = self._base_image_tag(
            build_coverage=build_coverage,
            use_asan=use_asan,
        )
        main_tag = self._main_image_tag(
            build_coverage=build_coverage,
            use_asan=use_asan,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            (temp_dir / 'base.Dockerfile').write_text(base)
            subprocess.run([
                'docker', 'build',
                '--platform', 'linux/amd64',
                '-t', base_tag,
                '-f', temp_dir / 'base.Dockerfile',
                str(temp_dir)
            ], check=True)

            res = subprocess.run([
                'docker', 'build',
                '--platform', 'linux/amd64',
                '--build-arg', f'BASE_IMAGE={base_tag}',
                '-t', main_tag,
                '-f', self.path / 'config' / 'Dockerfile',
                str((self.path / 'config').absolute())
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                stderr = res.stderr or ''
                if 'already exists' not in stderr:
                    # Real build failure; surface stdout/stderr to the caller
                    # and return no tag.
                    return BuildError(stdout=res.stdout, stderr=res.stderr), None

        # Successful build (or benign "already exists" race). Return no error
        # and the tag of the main image so callers can invoke it explicitly.
        return None, main_tag

    def invoke(
        self,
        env: List[str] = [],
        mounts: List[Tuple[str, str]] = [],
        cmd: str = 'bash',
        check: bool = False,
        image: Optional[str] = None,
        run_as_host_user: bool = True,
        popen: bool = False,
        use_tty: Optional[bool] = None,
        **kwargs,
    ):
        """
        Invoke a command inside the main container (must be built first).

        When running in an interactive terminal, we request a TTY from Docker
        (`-it`) so command output behaves like a normal shell session. When
        stdin is not a TTY (e.g. background workers or CI) we avoid `-t` to
        prevent Docker from failing with \"the input device is not a TTY\".

        Args:
            env: List of environment variables to copy from the host
            mounts: List of tuples of (host_path, container_path)
            cmd: Command to run
            run_as_host_user: If True, run the container as the invoking host
                UID:GID so that files created in mounted directories are owned
                by the caller. Set to False to fall back to the image's default
                user.
        """
        full_cmd = ['docker', 'run', '--rm']
        full_cmd += ['--platform', 'linux/amd64']

        # Only request a TTY when our own stdin is a TTY unless explicitly
        # overridden by callers. This keeps interactive usage unchanged while
        # allowing non-interactive callers (or callers managing signals) to
        # disable TTY behavior reliably.
        effective_tty = sys.stdin.isatty() if use_tty is None else bool(use_tty)
        if effective_tty:
            full_cmd.insert(2, '-it')

        # Decide whether to run the container as the host user. By default we
        # map the current process's UID/GID into the container so that files
        # created in mounted directories are owned by the caller. Callers can
        # opt out by setting run_as_host_user=False.
        if run_as_host_user:
            uid = getattr(os, 'getuid', lambda: None)()
            gid = getattr(os, 'getgid', lambda: None)()
            if uid is not None and gid is not None:
                full_cmd += ['--user', f'{uid}:{gid}']
            else:
                print(
                    '[agf] Warning: unable to determine host UID/GID; '
                    'running container as its default user',
                    file=sys.stderr,
                )

        for e in env:
            full_cmd += ['-e', f'{e}={os.environ.get(e, "")}']
        for mount in mounts:
            full_cmd += ['-v', f'{mount[0]}:{mount[1]}']
        full_cmd += ['--security-opt', f'seccomp={CUSTOM_SECCOMP}']

        # Prefer an explicitly provided image tag so callers can be precise
        # about which variant they want. If no image is given, fall back to the
        # historical default tag `main_<name>` to keep older workflows working.
        image_tag = image or f'main_{self.get_config().name}'

        full_cmd += [image_tag, 'bash', '-c', cmd]
        if popen:
            return subprocess.Popen(full_cmd, **kwargs)
        return subprocess.run(full_cmd, check=check, **kwargs)

    def next_harness_name(self) -> str:
        """
        Get the next harness name.
        """
        i = 1
        while True:
            name = f'harn{i}'
            if not (self.path / 'harnesses' / name).exists():
                return name
            i += 1
    
    def get_harness(self, name: str) -> 'Harness':
        """
        Get a harness by name.
        """
        return Harness(self.path / 'harnesses' / name)
