
from agf.data.project_info import ProjectInfo


class Agent:
    def __init__(self, info: ProjectInfo, model: str):
        self.info = info
        self.model = model

    def run(self):
        pass
