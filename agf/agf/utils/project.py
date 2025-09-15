from pathlib import Path
from typing import Optional, List, Tuple, Dict
from pydantic import BaseModel, Field
import os
import tempfile
import subprocess
import json

from ..data.metadata import UsageTracker

CUSTOM_SECCOMP = (Path(__file__).parent.parent / 'seccomp.json').absolute()


class ProjectConfig(BaseModel):
    repo: Optional[str] = Field(default=None, description='The url of the project')
    commit: Optional[str] = Field(default=None, description='The commit hash of the project')
    repro_per_crash: int = Field(default=20, description='The number of reproducers to generate for each crash')
    llm_min_per_crash: int = Field(default=1, description='Number of minimized test cases to generate for each crash')

    @property
    def name(self) -> str:
        if self.repo is None:
            return 'unknown'
        name = self.repo.split('github.com/')[1].split('.git')[0].replace('/', '_').replace('.', '_').lower()
        name = ''.join(x for x in name if x.isalnum() or x in ['_'])
        return name


class Harness:
    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()
    
    def create(self):
        self.path.mkdir(parents=True, exist_ok=True)

    def harness_config(self) -> Path:
        return self.path / 'harness.json'

    def output_dir(self) -> Path:
        output_dir = self.path / 'output'
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir
    
    def asan_output_dir(self) -> Path:
        output_dir = self.path / 'output_asan'
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir
    
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

    def has_build_config(self) -> bool:
        return (self.path / 'config').exists()

    def get_harnesses(self) -> List['Harness']:
        harness_path = self.path / 'harnesses'
        if not harness_path.exists():
            return []
        return [Harness(x) for x in harness_path.iterdir() if x.is_dir()]

    def base_image(self, build_coverage: bool = False, use_asan: bool = False) -> str:
        """
        Builds the base image for the project.
        """
        config = self.get_config()

        base_image = ''
        base_image += 'FROM gfafl_base\n'
        if config.repo is not None:
            if config.commit is not None:
                base_image += f'RUN git clone {config.repo} /fuzz/src && cd /fuzz/src && git checkout {config.commit} && git submodule update --init --remote --recursive\n'
            else:
                base_image += f'RUN git clone --depth 1 {config.repo} /fuzz/src && cd /fuzz/src && git submodule update --init --remote --recursive\n'
        base_image += 'WORKDIR /fuzz/src\n'
        base_image += 'ENV DEBIAN_FRONTEND=noninteractive\n'
        base_image += 'ENV LD_LIBRARY_PATH=/fuzz/install/lib\n'
        base_image += 'ENV ASAN_OPTIONS=hard_rss_limit_mb=256:detect_leaks=0\n'
        if build_coverage:
            base_image += 'ENV GRAPHFUZZ_BUILD_COVERAGE=1\n'
        if use_asan:
            base_image += 'ENV GRAPHFUZZ_USE_ASAN=1\n'
        return base_image

    def build_docker(self, build_coverage: bool = False, use_asan: bool = False) -> bool:
        config = self.get_config()
        base = self.base_image(build_coverage, use_asan)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            (temp_dir / 'base.Dockerfile').write_text(base)
            subprocess.run([
                'docker', 'build',
                '-t', f'base_{config.name}',
                '-f', temp_dir / 'base.Dockerfile',
                str(temp_dir)
            ], check=True)
        
            subprocess.run([
                'docker', 'build',
                '--build-arg', f'BASE_IMAGE=base_{config.name}',
                '-t', f'main_{config.name}',
                '-f', self.path / 'config' / 'Dockerfile',
                str((self.path / 'config').absolute())
            ], check=True)

    def invoke(self, env: List[str] = [], mounts: List[Tuple[str, str]] = [], cmd: str = 'bash', check: bool = False):
        """
        Invoke a command inside the main container (must be built first).

        Args:
            env: List of environment variables to copy from the host
            mounts: List of tuples of (host_path, container_path)
            cmd: Command to run
        """
        full_cmd = ['docker', 'run', '-it', '--rm']
        for e in env:
            full_cmd += ['-e', f'{e}={os.environ.get(e, "")}']
        for mount in mounts:
            full_cmd += ['-v', f'{mount[0]}:{mount[1]}']
        full_cmd += ['--security-opt', f'seccomp={CUSTOM_SECCOMP}']
        full_cmd += [f'main_{self.get_config().name}', 'bash', '-c', cmd]
        subprocess.run(full_cmd, check=check)

    def next_harness_name(self) -> str:
        """
        Get the next harness name.
        """
        i = 0
        while True:
            name = f'harness_{i}'
            if not (self.path / 'harnesses' / name).exists():
                return name
            i += 1
    
    def get_harness(self, name: str) -> 'Harness':
        """
        Get a harness by name.
        """
        return Harness(self.path / 'harnesses' / name)
