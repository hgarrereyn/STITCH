from typing import Tuple, List, Optional
from dataclasses import dataclass
import tiktoken
from abc import ABC, abstractmethod
from pathlib import Path
import json


@dataclass
class Chunk:
    filename: str
    start_point: Tuple[int, int]
    end_point: Tuple[int, int]
    text: str

    def pretty_print(self):
        tokens = tiktoken.get_encoding('cl100k_base').encode(self.text)
        print('\n' * 2)
        print('-' * 80)
        print(f'{self.filename}:{self.start_point} - {self.end_point}')
        print(f'Tokens: {len(tokens)}')
        print('-' * 80)
        print(self.text)
        print('-' * 80)

    def serialize(self):
        return json.dumps({
            'filename': str(self.filename),
            'start_point': self.start_point,
            'end_point': self.end_point,
            'text': self.text
        })
    
    @staticmethod
    def deserialize(data: str):
        data = json.loads(data)
        return Chunk(
            filename=str(data['filename']),
            start_point=data['start_point'],
            end_point=data['end_point'],
            text=data['text']
        )

class Chunker(ABC):
    @abstractmethod
    def chunk(self, fpath: Path, text: str) -> List[Chunk]:
        pass
