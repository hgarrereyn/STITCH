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
from ..utils.colors import Colors
import tiktoken
import h5py


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
    # HDF5 file with two keys:
    # - 'chunks': list of serialized chunks
    # - 'embeddings': numpy array of embeddings
    rag_file: h5py.File

    def _load_codebase(self, fpath: Path) -> List[Chunk]:
        chunks = []
        for path in fpath.rglob('*'):
            if path.is_file():
                for config in CHUNKERS:
                    if any(path.match(pattern) for pattern in config.matching):
                        if not any(path.match(pattern) for pattern in config.ignore_matching):
                            with open(path, 'r', encoding='utf-8') as file:
                                content = file.read()
                            chunks.extend(config.chunker.chunk(path, content))
                        break
        return chunks

    @staticmethod
    def new(fpath: Path):
        rag_file = h5py.File(fpath, 'w')
        return RAG(rag_file=rag_file)

    @staticmethod
    def load(fpath: Path):
        rag_file = h5py.File(fpath, 'r')
        return RAG(rag_file=rag_file)

    def batch_embed(self, codebase_path: Path, batch_size: int = 100, model: str = "text-embedding-3-small") -> None:
        """
        Generate embeddings for all chunks in batches for better efficiency.
        
        Args:
            batch_size: Number of chunks to process in each batch
            model: The model to use for embeddings
        """
        chunks = self._load_codebase(codebase_path)

        # Filter out chunks that are too large to embed
        chunks_to_embed = [chunk for chunk in chunks if len(tiktoken.get_encoding('cl100k_base').encode(chunk.text)) < 8192]
       
        # Embed one text to get the embedding dimension
        response = openai.embeddings.create(
            input=chunks_to_embed[0].text,
            model=model
        )
        embedding_dim = len(response.data[0].embedding)
       
        embeddings = np.zeros((len(chunks_to_embed), embedding_dim))
        
        for i in tqdm(range(0, len(chunks_to_embed), batch_size), desc="Embedding chunks"):
            batch = chunks_to_embed[i:i + batch_size]
            texts = [chunk.text for chunk in batch]
            
            # Get embeddings for the batch
            response = openai.embeddings.create(
                input=texts,
                model=model
            )
            
            # Update chunks with their embeddings
            for j, embedding_data in enumerate(response.data):
                embeddings[i + j] = embedding_data.embedding

        print("Saving embeddings and chunks to HDF5 file...")
        self.rag_file['embeddings'] = embeddings
        self.rag_file['chunks'] = [chunk.serialize() for chunk in chunks_to_embed]
        print("Done!")

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

        # Calculate similarities with all chunks
        similarities = np.dot(self.rag_file['embeddings'], query_embedding) / (
            np.linalg.norm(self.rag_file['embeddings']) * np.linalg.norm(query_embedding)
        )
        # Sort by similarity and return top N chunks
        sorted_indices = np.argsort(similarities)[::-1]
        return [(Chunk.deserialize(self.rag_file['chunks'][i]), float(similarities[i])) for i in sorted_indices[:top_n]]


def register(subparsers):
    rag_parser = subparsers.add_parser('rag', help='RAG CLI tool for code search')
    sub = rag_parser.add_subparsers()
    
    # Embed command
    embed_parser = sub.add_parser('embed', help='Load codebase, embed chunks, and save')
    embed_parser.add_argument('codebase_path', type=str, help='Path to the codebase')
    embed_parser.add_argument('save_path', type=str, help='Path to save the RAG object')
    embed_parser.add_argument('--batch-size', type=int, default=100, help='Batch size for embeddings')
    embed_parser.add_argument('--model', type=str, default="text-embedding-3-small", help='Model to use for embeddings')
    embed_parser.set_defaults(func=embed_main)
    
    # Query command
    query_parser = sub.add_parser('query', help='Load RAG and search for matches')
    query_parser.add_argument('save_path', type=str, help='Path to the saved RAG object')
    query_parser.add_argument('query_text', type=str, help='Text to search for')
    query_parser.add_argument('--top-n', type=int, default=5, help='Number of top matches to return')
    query_parser.add_argument('--model', type=str, default="text-embedding-3-small", help='Model to use for embeddings')
    query_parser.set_defaults(func=query_main)


def embed_main(args):
    # Create new RAG instance and load codebase
    rag = RAG.new(Path(args.save_path))
    
    # Embed chunks
    rag.batch_embed(Path(args.codebase_path), batch_size=args.batch_size, model=args.model)
    
    rag.rag_file.close()
    # Save RAG object
    print(f"Saved embedded RAG to {args.save_path}")


def query_main(args):
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
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()
