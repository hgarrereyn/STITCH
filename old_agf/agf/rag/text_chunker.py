from typing import List
from pathlib import Path
import sys
from .chunk import Chunk, Chunker


class TextChunker(Chunker):
    def __init__(self, max_chunk_size: int, overlap_size: int):
        self.max_chunk_size = max_chunk_size
        self.overlap_size = overlap_size

    def chunk(self, fpath: Path, text: str) -> List[Chunk]:
        chunks = []
        
        for i in range(0, len(text), self.max_chunk_size - self.overlap_size):
            chunk_text = text[i:i + self.max_chunk_size]
            chunks.append(Chunk(fpath, (i, 0), (i + self.max_chunk_size, 0), chunk_text))
        
        return chunks

if __name__ == '__main__':
    chunker = TextChunker(max_chunk_size=1000, overlap_size=100)
    fpath = Path(sys.argv[1])
    with open(fpath, 'r') as f:
        text = f.read()
    chunks = chunker.chunk(fpath, text)
    for chunk in chunks:
        chunk.pretty_print()
