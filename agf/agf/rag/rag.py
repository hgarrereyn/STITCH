import numpy as np
from typing import Optional, List, Set, Dict, Protocol
from pathlib import Path
from dataclasses import dataclass
import tiktoken
import openai
from tqdm.auto import tqdm
import pandas as pd
import json
from abc import ABC, abstractmethod

from .tree_chunker import TreeSitterChunker
from .overlapping_chunker import OverlappingChunker
from .chunker import Chunker, Chunk


DEFAULT_CHUNKER = OverlappingChunker(max_chunk_size=1000, overlap_size=500)

CHUNKERS = [
    (TreeSitterChunker(max_chunk_size=1000), {'.c', '.h', '.cpp', '.hpp', '.c++', '.h++', '.cc', '.hh'}),
]

@dataclass
class RAG:
    chunks: List[Chunk]
    
    @classmethod
    def from_path(
        cls,
        path: Path,
    ) -> 'RAG':
        """
        Create a Rag instance by loading text chunks from files in the given path.
        
        Args:
            path: Root path to recursively search for files

        Returns:
            Rag instance containing the chunks
        """
        chunks = []
        
        # Create a mapping of file extensions to their chunkers
        extension_to_chunker = {}
        for chunker, extensions in CHUNKERS:
            for ext in extensions:
                extension_to_chunker[ext] = chunker
        
        # Process all files recursively
        for file_path in tqdm(list(path.rglob('*')), desc="Processing files"):
            if not file_path.is_file():
                continue
                
            # Skip hidden files and directories
            if any(part.startswith('.') for part in file_path.parts):
                continue
                
            try:
                # Get the appropriate chunker based on file extension
                chunker = extension_to_chunker.get(file_path.suffix, DEFAULT_CHUNKER)
                
                # Read and chunk the file
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    file_chunks = chunker.chunk(file_path, content)
                    chunks.extend(file_chunks)
                    
            except Exception as e:
                print(f"Error processing {file_path}: {e}")
                continue
        
        return cls(chunks=chunks)

    def batch_embed(self, batch_size: int = 100, model: str = "text-embedding-3-small") -> None:
        """
        Generate embeddings for all chunks in batches for better efficiency.
        
        Args:
            batch_size: Number of chunks to process in each batch
            model: The model to use for embeddings
        """
        # Filter chunks that don't have embeddings yet
        chunks_to_embed = [chunk for chunk in self.chunks if chunk.embedding is None]
        
        for i in tqdm(range(0, len(chunks_to_embed), batch_size), desc="Embedding chunks"):
            batch = chunks_to_embed[i:i + batch_size]
            texts = [chunk.text for chunk in batch]
            
            # Get embeddings for the batch
            response = openai.embeddings.create(
                input=texts,
                model=model
            )
            
            # Update chunks with their embeddings
            for chunk, embedding_data in zip(batch, response.data):
                chunk.embedding = np.array(embedding_data.embedding)
                
    def query(self, query_text: str, top_n: int = 5, model: str = "text-embedding-3-small") -> List[tuple[Chunk, float]]:
        """
        Find the most relevant chunks for a given query using cosine similarity.
        
        Args:
            query_text: The query text to search for
            top_n: Number of most relevant chunks to return
            model: Model to use for embedding the query
            
        Returns:
            List of tuples containing (chunk, similarity_score) sorted by relevance
        """
        # Ensure all chunks have embeddings
        if any(chunk.embedding is None for chunk in self.chunks):
            raise ValueError("Some chunks don't have embeddings. Call batch_embed() first.")
            
        # Get query embedding
        query_response = openai.embeddings.create(
            input=[query_text],
            model=model
        )
        query_embedding = np.array(query_response.data[0].embedding)
        
        # Calculate cosine similarity with all chunks
        chunk_scores = []
        for chunk in self.chunks:
            # Compute cosine similarity
            similarity = np.dot(query_embedding, chunk.embedding) / (
                np.linalg.norm(query_embedding) * np.linalg.norm(chunk.embedding)
            )
            chunk_scores.append((chunk, float(similarity)))
        
        # Sort by similarity score in descending order and return top N
        return sorted(chunk_scores, key=lambda x: x[1], reverse=True)[:top_n]
        
    def save(self, path: Path) -> None:
        """
        Save the RAG index to a CSV file, storing metadata but not the actual content.
        Content will be reloaded from source files when loading.
        
        Args:
            path: Path to save the CSV file
        """
        # Convert chunks to serializable format
        data = []
        for chunk in self.chunks:
            data.append({
                'source': str(chunk.source),
                'start': chunk.start,
                'end': chunk.end,
                'embedding': json.dumps(chunk.embedding.tolist()) if chunk.embedding is not None else None
            })
            
        # Save to CSV
        df = pd.DataFrame(data)
        df.to_csv(path, index=False)
        
    @classmethod
    def load(cls, path: Path) -> 'RAG':
        """
        Load a RAG index from a CSV file, reading content from source files.
        
        Args:
            path: Path to the CSV file containing the RAG index
            
        Returns:
            Loaded Rag instance
        """
        df = pd.read_csv(path)
        chunks = []
        
        for _, row in tqdm(df.iterrows(), desc="Loading chunks"):
            source = Path(row['source'])
            try:
                # Read the content from the source file
                with open(source, 'r') as f:
                    content = f.read()
                    chunk_text = content[row['start']:row['end']]
                    
                # Create chunk with loaded content and metadata
                chunk = Chunk(
                    source=source,
                    start=row['start'],
                    end=row['end'],
                    text=chunk_text,
                    embedding=np.array(json.loads(row['embedding'])) if pd.notna(row['embedding']) else None
                )
                chunks.append(chunk)
                
            except Exception as e:
                print(f"Error loading chunk from {source}: {e}")
                continue
                
        return cls(chunks=chunks)

