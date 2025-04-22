from typing import Tuple, List
from dataclasses import dataclass
import os
import sys
import tiktoken
from pathlib import Path

from tree_sitter import Node
from tree_sitter_languages import get_parser

from .chunk import Chunk, Chunker


class CppChunker(Chunker):
    CHUNK_TYPES = [
        'function_definition',
        'declaration',
        'field_declaration',
        'attributed_statement',
        'type_definition',
        'preproc_def',
        'preproc_function_def',
        'preproc_call',

        'enum_specifier',
        'struct_specifier',
        'union_specifier',
        'class_specifier',

        'static_assert_declaration',
    ]

    CONTAINER_TYPES = [
        'linkage_specification',
        'preproc_if',
        'preproc_ifdef',
        'preproc_else',
        'preproc_elif',
        'declaration_list',
        'class_specifier',
        'field_declaration_list',
        'namespace_definition',
    ]

    IGNORE_TYPES = [
        'comment',
        'identifier',
        ';',
        '#ifdef',
        '#ifndef',
        '#if',
        '#else',
        '#endif',
        '#elif',
        'preproc_include',
        'expression_statement',
        'ERROR',
        'class',
        'type_identifier',
        '{',
        '}',
        'parenthesized_expression',
        '\n',
        'binary_expression',
        'unary_expression',
        'preproc_defined',
        'extern',
        'string_literal',
        'for_statement',
        'if_statement',
        'return_statement',
        'function_declarator',
        'for',
        'if',
        '(',
        ')',
        'assignment_expression',
        'pointer_expression',
        'update_expression',
        'condition_clause',
        'compound_statement',
        'number_literal',
        'labeled_statement',
        'switch_statement',
        'call_expression',
        'storage_class_specifier',
        'primitive_type',
        'while_statement',
        'using_declaration',
        'base_class_clause',
        'access_specifier',
        ':',
        'template_declaration',
        'namespace',
        'namespace_identifier',
        'goto_statement',
        'while',
        'compound_literal_expression',
        '*',
        'else',
        'case_statement',
        'pointer_declarator',
        'switch',
        'case',
        ',',
        '-',
    ]

    HIERARCHY_TYPES = [
        'preproc_ifdef',
        'preproc_else',
        'preproc_elif',
        'preproc_if',
        'class_specifier',
        'linkage_specification',
        'namespace_definition',
    ]

    HIERARCHY_IGNORE = [
        'translation_unit',
        'field_declaration_list',
        'declaration_list',
        'ERROR',
    ]

    def __init__(self, max_chunk_size: int, encoding: str = 'cl100k_base'):
        self.max_chunk_size = max_chunk_size
        self.parser = get_parser('cpp')
        self.tokenizer = tiktoken.get_encoding(encoding)

    def chunk(self, filename: str, text: str) -> List[Chunk]:
        tree = self.parser.parse(text.encode())
        root = tree.root_node

        chunks = []
        self._generate_chunks(root, filename, chunks)
        return chunks
    
    def _generate_chunks(self, parent: Node, filename: str, chunks: List[Chunk]):
        for child in parent.children:
            typ = child.type

            if typ in self.CHUNK_TYPES:
                self._add_chunk(child, parent, filename, chunks)
            # elif typ == 'comment':
            #     self._add_comment(child, parent, filename, chunks)

            if typ in self.CONTAINER_TYPES:
                self._generate_chunks(child, filename, chunks)
            elif typ in self.IGNORE_TYPES:
                continue
            elif typ not in self.CHUNK_TYPES:
                print('unknown', typ, filename, child.start_point, child.end_point, child.text.decode())
                raise Exception('unknown', typ, child.text.decode())

    def _add_comment(self, node: Node, parent: Node, filename: str, chunks: List[Chunk]):
        # only add comments that are not connected to another construct
        next = node.next_sibling
        if next and next.start_point[0] <= node.end_point[0] + 1:
            return
        
        text = self._format_chunk(node, parent, filename, attach_comments=False)
        chunks.append(Chunk(filename, node.start_point, node.end_point, text))

    def _add_chunk(self, node: Node, parent: Node, filename: str, chunks: List[Chunk]):
        text = self._format_chunk(node, parent, filename)
        tokens = self.tokenizer.encode(text)
        if len(tokens) > self.max_chunk_size:
            self._add_outline(node, filename, chunks)
        else:
            chunks.append(Chunk(filename, node.start_point, node.end_point, text))

    def _add_outline(self, node: Node, filename: str, chunks: List[Chunk]):
        print('--- outline ---')
        # print(node.text.decode())

    def _get_hierarchy(self, node: Node) -> List[Node]:
        hierarchy = []
        current = node.parent
        while current:
            hierarchy.append(current)
            current = current.parent
        return hierarchy[::-1]

    def _format_chunk(self, node: Node, parent: Node, filename: str, attach_comments: bool = True) -> str:
        # print('formatting chunk', node.type, node.start_point, node.end_point, parent.type)

        inner = node.text.decode()

        comments = []
        if attach_comments:
            line = node.start_point[0]
            current = node
            while True:
                prev = current.prev_sibling
                if prev and prev.type == 'comment' and prev.end_point[0] >= line - 1:
                    comments.append(prev)
                    line = prev.start_point[0]
                    current = prev
                else:
                    break

        inner = '\n'.join(map(lambda c: c.text.decode(), comments[::-1])) + '\n' + inner

        # Generate hierarchy if needed
        pre = ''

        hierarchy = self._get_hierarchy(node)
        for h in hierarchy:
            if h.type in self.HIERARCHY_TYPES:
                line = h.text.decode().strip().split('\n')[0]
                pre += line + '\n'
            elif h.type not in self.HIERARCHY_IGNORE:
                print('hierarchy unknown', h.type, h.text.decode())
                raise Exception('hierarchy unknown', h.type, h.text.decode())
        if pre != '':
            pre = '// <file context>\n' + pre.strip() + '\n// </file context>\n'

        start = node.start_point
        end = node.end_point
        if len(comments) > 0:
            start = comments[0].start_point

        meta = f'// {filename} - {start[0]}:{start[1]} - {end[0]}:{end[1]}\n'

        text = meta + pre + inner

        return text

if __name__ == '__main__':

    total_chunks = []

    codebase = Path(sys.argv[1])
    chunker = CppChunker(max_chunk_size=1000)
    for fpath in codebase.rglob('*.c'):
        with open(fpath, 'r') as f:
            text = f.read()
        chunks = chunker.chunk(fpath, text)
        total_chunks.extend(chunks)

    print(len(total_chunks))

    # compute the number of tokens
    total_tokens = sum(map(lambda c: len(tiktoken.get_encoding('cl100k_base').encode(c.text)), total_chunks))
    print(total_tokens)
