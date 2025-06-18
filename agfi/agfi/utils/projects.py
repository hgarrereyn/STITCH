import os
from pathlib import Path
from typing import List


BUILD_DIR = Path(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'build'))
BUILD_DIR = BUILD_DIR.resolve()


class ProjectContext:
    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def root_context() -> 'ProjectContext':
        ctx = ProjectContext(BUILD_DIR)
        ctx.setup()
        return ctx

    def setup(self):
        self.path.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> List[str]:
        return [x.name for x in self.path.iterdir() if x.is_dir()]
    
    def get_project(self, project_name: str) -> 'Project':
        return Project(self.path / project_name)

class Project:
    def __init__(self, path: Path):
        self.path = path

    def setup(self):
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / 'harnesses').mkdir(parents=True, exist_ok=True)
    
    def list_harnesses(self) -> List[str]:
        return [x.name for x in (self.path / 'harnesses').iterdir() if x.is_dir()]

    def get_harness(self, harness_name: str) -> 'ProjectHarness':
        return ProjectHarness(self.path / 'harnesses' / harness_name)
    
    def next_harness_name(self) -> str:
        i = 0
        while True:
            name = f'harness_{i}'
            if not (self.path / 'harnesses' / name).exists():
                return name
            i += 1

    @property
    def base_image(self) -> Path:
        return self.path / 'base.Dockerfile'
    
    @property
    def dockerfile(self) -> Path:
        return self.path / 'Dockerfile'
    
    @property
    def info(self) -> Path:
        return self.path / 'info.json'

class ProjectHarness:
    def __init__(self, path: Path):
        self.path = path

    def setup(self):
        self.path.mkdir(parents=True, exist_ok=True)
