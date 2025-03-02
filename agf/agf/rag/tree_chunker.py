from typing import List, Optional, Tuple
from pathlib import Path

from .chunker import Chunker, Chunk

from tree_sitter import Node, Tree
from tree_sitter_languages import get_parser


class TreeSitterChunker(Chunker):
    def __init__(self, max_chunk_size: int, overlap_size: int = 0, **kwargs):
        super().__init__(max_chunk_size, **kwargs)
        # Initialize tree-sitter parser for C++
        self.parser = get_parser('cpp')

    def _get_context_prefix_postfix(self, node: Node, source_text: str) -> Tuple[str, str]:
        """Build a context prefix showing the hierarchical path to this node"""
        parts = []
        parent = node.parent
        
        while parent and parent.type != "translation_unit":
            text = source_text[parent.start_byte:parent.end_byte]
            first_line = text.split('\n')[0].strip()
            if not first_line.endswith('{'):
                first_line += ' {'

            parts.append(first_line)
            parent = parent.parent
            
        prefix = ''
        postfix = ''
        depth = 0

        for part in reversed(parts):
            ind = '  ' * depth
            prefix += f'{ind}{part}\n{ind}  // ...\n'
            postfix = f'{ind}  // ...\n{ind}}}\n'
            depth += 1

        return prefix, postfix

    def _should_create_chunk(self, node: Node) -> bool:
        """Determine if this node should be its own chunk"""
        interesting_types = {
            'function_definition',
            'method_definition', 
            'constructor_definition',
            'destructor_definition',
            'class_definition',
            'struct_definition',
            'enum_definition',
            'namespace_definition',
            'declaration',  # Added to catch function declarations in headers
            'function_declarator'  # Added to catch function declarations in headers
        }
        return node.type in interesting_types

    def _get_preceding_comment(self, node: Node, source_text: str) -> str:
        """Get any documentation comment that precedes this node"""
        # Get the start of the current line
        line_start = node.start_byte
        while line_start > 0 and source_text[line_start - 1] != '\n':
            line_start -= 1
            
        # Look for comments before this line
        comment_end = line_start
        while comment_end > 0:
            # Skip whitespace
            comment_end -= 1
            while comment_end > 0 and source_text[comment_end - 1].isspace():
                comment_end -= 1
            if comment_end <= 0:
                break
                
            # Check if we have a comment block ending
            if comment_end >= 2 and source_text[comment_end - 2:comment_end] == '*/':
                # Found end of comment block, now find its start
                comment_start = source_text.rfind('/*', 0, comment_end - 2)
                if comment_start >= 0:
                    comment = source_text[comment_start:comment_end]
                    # Only return if it looks like a documentation comment
                    if comment.startswith('/**') or comment.startswith('/*!'):
                        # Count parent nodes to determine indentation
                        depth = 0
                        parent = node.parent
                        while parent and parent.type != "translation_unit":
                            depth += 1
                            parent = parent.parent
                        # Add proper indentation to each line
                        indent = '  ' * depth
                        comment_lines = comment.split('\n')
                        indented_comment = '\n'.join(indent + line.strip() for line in comment_lines)
                        return indented_comment + '\n'
                break
                
            # If we hit non-whitespace that's not a comment end, stop looking
            if not source_text[comment_end - 1].isspace():
                break
                
        return ''

    def _visit_node(self, node: Node, source_text: str, chunks: List[Chunk], file_path: Path):
        """Recursively visit nodes and create chunks"""
        if self._should_create_chunk(node):
            # Create context prefix showing the hierarchical path
            prefix, postfix = self._get_context_prefix_postfix(node, source_text)
            
            comment = self._get_preceding_comment(node, source_text)
            node_text = source_text[node.start_byte:node.end_byte]
            
            # Combine context, comment and node text
            full_text = prefix + comment + node_text + '\n' + postfix
            
            chunks.append(Chunk(
                source=file_path,
                start=node.start_byte,
                end=node.end_byte,
                text=full_text,
                embedding=None
            ))
        
        # Recursively visit children
        for child in node.children:
            self._visit_node(child, source_text, chunks, file_path)

    def chunk(self, file_path: Path, text: str) -> List[Chunk]:
        try:
            tree = self.parser.parse(bytes(text, "utf8"))
            chunks: List[Chunk] = []
            self._visit_node(tree.root_node, text, chunks, file_path)
            return chunks
        except Exception as e:
            # If parsing fails, fall back to treating the whole file as one chunk
            return [Chunk(
                source=file_path,
                start=0,
                end=len(text),
                text=text,
                embedding=None
            )]

