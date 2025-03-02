from typing import Tuple, List, Optional
from dataclasses import dataclass
import tiktoken
from abc import ABC, abstractmethod
from pathlib import Path


@dataclass
class Chunk:
    filename: str
    start_point: Tuple[int, int]
    end_point: Tuple[int, int]
    text: str
    embedding: Optional[List[float]] = None

    def pretty_print(self):
        tokens = tiktoken.get_encoding('cl100k_base').encode(self.text)
        print('\n' * 2)
        print('-' * 80)
        print(f'{self.filename}:{self.start_point} - {self.end_point}')
        print(f'Tokens: {len(tokens)}')
        print('-' * 80)
        print(self.text)
        print('-' * 80)


class Chunker(ABC):
    @abstractmethod
    def chunk(self, fpath: Path, text: str) -> List[Chunk]:
        pass
