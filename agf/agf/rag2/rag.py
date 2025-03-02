from typing import List, Tuple
from dataclasses import dataclass
from pathlib import Path
import pickle
import numpy as np
from tqdm.auto import tqdm
import openai
import argparse
from .cpp_chunker import CppChunker
from .text_chunker import TextChunker
from .chunk import Chunk, Chunker
from ..colors import Colors


@dataclass
class Config:
    chunker: Chunker
    matching: List[str]
    ignore_matching: List[str]


CHUNKERS = [
    Config(
        chunker=CppChunker(max_chunk_size=1000),
        matching=[
            '*.c',
            '*.h',
            '*.cpp',
            '*.hpp',
            '*.cc',
            '*.hh',
            '*.cxx',
            '*.hxx',
        ],
        ignore_matching=[]
    ),
    Config(
        chunker=TextChunker(max_chunk_size=1000, overlap_size=500),
        matching=[
            '*.md',
            '*.txt',
            '*.rst',
            'README',
            'USAGE'
        ],
        ignore_matching=[
            'CMakeLists.txt',
            'CMakeCache.txt',
        ]
    ),
]


@dataclass
class RAG:
    chunks: List[Chunk]

    def load_codebase(self, fpath: Path):
        for path in fpath.rglob('*'):
            if path.is_file():
                for config in CHUNKERS:
                    if any(path.match(pattern) for pattern in config.matching):
                        if not any(path.match(pattern) for pattern in config.ignore_matching):
                            with open(path, 'r', encoding='utf-8') as file:
                                content = file.read()
                            self.chunks.extend(config.chunker.chunk(path, content))
                        break

    def save(self, fpath: Path):
        with open(fpath, 'wb') as f:
            pickle.dump(self, f)

    @staticmethod
    def load(fpath: Path):
        with open(fpath, 'rb') as f:
            return pickle.load(f)

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
                chunk.embedding = embedding_data.embedding

    def query(self, query_text: str, top_n: int = 5, model: str = "text-embedding-3-small") -> List[Tuple[Chunk, float]]:
        """
        Query the RAG system with a text query and return the top N matching chunks.
        
        Args:
            query_text: The text to search for
            top_n: Number of top matches to return
            model: The model to use for embeddings
            
        Returns:
            List of tuples containing (chunk, similarity_score)
        """
        # Get query embedding
        response = openai.embeddings.create(
            input=[query_text],
            model=model
        )
        query_embedding = np.array(response.data[0].embedding)
        
        # Calculate similarities with all chunks that have embeddings
        similarities = []
        for chunk in self.chunks:
            if chunk.embedding is not None:
                chunk_embedding = np.array(chunk.embedding)
                similarity = np.dot(query_embedding, chunk_embedding) / (
                    np.linalg.norm(query_embedding) * np.linalg.norm(chunk_embedding)
                )
                similarities.append((chunk, float(similarity)))
        
        # Sort by similarity and return top N
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_n]


def main():
    parser = argparse.ArgumentParser(description='RAG CLI tool for code search')
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Embed command
    embed_parser = subparsers.add_parser('embed', help='Load codebase, embed chunks, and save')
    embed_parser.add_argument('codebase_path', type=str, help='Path to the codebase')
    embed_parser.add_argument('save_path', type=str, help='Path to save the RAG object')
    embed_parser.add_argument('--batch-size', type=int, default=100, help='Batch size for embeddings')
    embed_parser.add_argument('--model', type=str, default="text-embedding-3-small", help='Model to use for embeddings')
    
    # Query command
    query_parser = subparsers.add_parser('query', help='Load RAG and search for matches')
    query_parser.add_argument('save_path', type=str, help='Path to the saved RAG object')
    query_parser.add_argument('query_text', type=str, help='Text to search for')
    query_parser.add_argument('--top-n', type=int, default=5, help='Number of top matches to return')
    query_parser.add_argument('--model', type=str, default="text-embedding-3-small", help='Model to use for embeddings')
    
    args = parser.parse_args()
    
    if args.command == 'embed':
        # Create new RAG instance and load codebase
        rag = RAG(chunks=[])
        rag.load_codebase(Path(args.codebase_path))
        
        # Embed chunks
        print(f"Embedding {len(rag.chunks)} chunks...")
        rag.batch_embed(batch_size=args.batch_size, model=args.model)
        
        # Save RAG object
        rag.save(Path(args.save_path))
        print(f"Saved embedded RAG to {args.save_path}")
        
    elif args.command == 'query':
        # Load RAG object
        rag = RAG.load(Path(args.save_path))
        
        # Perform query
        results = rag.query(args.query_text, top_n=args.top_n, model=args.model)
        
        # Print results
        print(f"\n{Colors.BOLD}Top {len(results)} matches for query:{Colors.END} {Colors.CYAN}{args.query_text}{Colors.END}\n")
        for i, (chunk, similarity) in enumerate(results, 1):
            print(f"{Colors.BOLD}Match {i}{Colors.END} ({Colors.GREEN}similarity: {similarity:.3f}{Colors.END}):")
            print(f"{Colors.YELLOW}File:{Colors.END} {chunk.filename}")
            print(f"{Colors.YELLOW}Location:{Colors.END} {chunk.start_point} - {chunk.end_point}")
            print(f"{Colors.YELLOW}Content:{Colors.END}")
            print(f"{Colors.LIGHT_GRAY}{'-' * 80}{Colors.END}")
            print(chunk.text)
            print(f"{Colors.LIGHT_GRAY}{'-' * 80}{Colors.END}")
            print()


if __name__ == '__main__':
    main()
