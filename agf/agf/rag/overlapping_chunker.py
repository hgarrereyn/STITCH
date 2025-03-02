from typing import List
from pathlib import Path

from .chunker import Chunker, Chunk

class OverlappingChunker(Chunker):
    """Default chunking strategy that splits text into overlapping chunks of fixed size."""
    
    def __init__(self, max_chunk_size: int, overlap_size: int = 0, **kwargs):
        super().__init__(max_chunk_size, **kwargs)
        self.overlap_size = overlap_size

    def chunk(self, file_path: Path, content: str) -> List['Chunk']:
        chunks = []
        start = 0
        
        while start < len(content):
            end = min(start + self.chunk_size, len(content))
            chunk_text = content[start:end]
            
            chunks.append(Chunk(
                source=file_path,
                start=start,
                end=end,
                text=chunk_text,
                embedding=None
            ))
            
            # Move start position accounting for overlap
            start = end - self.overlap_size if end < len(content) else len(content)
        
        return chunks
