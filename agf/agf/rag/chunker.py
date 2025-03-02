from abc import ABC, abstractmethod
from typing import List, Optional
from pathlib import Path
from dataclasses import dataclass

import tiktoken
import numpy as np


@dataclass
class Chunk:
    source: Path
    start: int
    end: int
    text: str
    embedding: Optional[np.ndarray]
    
    def num_tokens(self, model: str = "gpt-4-0125-preview") -> int:
        """
        Calculate the number of tokens in the chunk's text using tiktoken.
        
        Args:
            model: The model name to use for tokenization (default: gpt-4-0125-preview)
            
        Returns:
            Number of tokens in the chunk's text
        """
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            # Fallback to cl100k_base encoding if model not found
            encoding = tiktoken.get_encoding("cl100k_base")
            
        return len(encoding.encode(self.text))


class Chunker(ABC):
    """Abstract base class for file content chunking strategies."""
    
    def __init__(self, max_chunk_size: int, **kwargs):
        self.max_chunk_size = max_chunk_size
    
    @abstractmethod
    def chunk(self, file_path: Path, content: str) -> List['Chunk']:
        """Split file content into chunks according to the chunking strategy.
        
        Args:
            file_path: Path to the source file
            content: Content of the file to chunk
            
        Returns:
            List of Chunk objects
        """
        pass
