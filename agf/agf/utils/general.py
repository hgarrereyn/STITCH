from typing import List


def _split_file_into_chunks(content: str, chunk_size: int) -> List[str]:
    """Split a file into chunks of roughly equal size, trying to break at reasonable boundaries."""
    lines = content.split('\n')
    chunks = []
    current_chunk = []
    current_size = 0
    
    for line in lines:
        current_chunk.append(line)
        current_size += 1
        
        # When we hit chunk size, try to find a good break point
        if current_size >= chunk_size:
            # Look for a blank line or end of block/statement
            if line.strip() == '' or line.strip().endswith(';') or line.strip().endswith('}'):
                chunks.append('\n'.join(current_chunk))
                current_chunk = []
                current_size = 0
    
    # Add any remaining lines as the last chunk
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
    
    return chunks


def split_into_chunks(files: List[str], chunk_size: int) -> List[str]:
    """Split a list of header files into chunks of roughly equal size, trying to break at reasonable boundaries."""        
    # Process all files and split into chunks
    chunks = []
    for file in files:
        with open(file, 'r') as f:
            content = f.read()
        chunks.extend(_split_file_into_chunks(content, chunk_size))

    return chunks
