import argparse
from typing import List
import subprocess
from pydantic import BaseModel
from pathlib import Path


class TagResult(BaseModel):
    full_ctag_line: str
    filename: str
    line: int
    kind: str
    file_preview: str

    @staticmethod
    def from_line(line: str) -> 'TagResult':
        '''Parse ctags output line into a TagResult'''
        # ctags output line format:
        # identifier<tab>filename<tab>/^<regex>;"<tab>extra_fields
        parts = line.split('\t')
        # identifier = parts[0]
        filename = parts[1]
        
        lineno = None
        kind = None

        extra_fields = ';"\t'.join(line.split(';"\t')[1:]).split('\t')
        for field in extra_fields:
            if field.startswith('line:'):
                lineno = int(field.split(':')[1])
            elif field.startswith('kind:'):
                kind = field.split(':')[1]

        # get file preview
        file_lines = Path(filename).read_text().splitlines()
        snippet_range = list(range(max(0, lineno-5), min(len(file_lines), lineno+25)))
        snippet = [(i, line) for i, line in enumerate(file_lines) if i in snippet_range]
        snippet = [f'{i+1:4d}| {line}' for i, line in snippet]
        file_preview = '\n'.join(snippet)

        return TagResult(full_ctag_line=line, filename=filename, line=lineno, kind=kind, file_preview=file_preview)

    def pprint(self) -> str:
        return f'''
--------------------
{self.full_ctag_line}
--------------------
(File: {self.filename})
{self.file_preview}
'''

def run_ctags(codebase: str, tag_file: str):
    subprocess.run([
        'ctags',
        '-R',
        '-f', tag_file,
        '--languages=C,C++',
        '--extras=+q',
        '--fields=afiKmnszt',
        codebase
    ], check=True)


def search_ctags(codebase: str, identifier: str) -> List[TagResult]:
    tag_file = Path(codebase) / 'tags'
    if not tag_file.exists():
        run_ctags(codebase, tag_file)

    tag_lines = tag_file.read_text().splitlines()
    results = []
    for line in tag_lines:
        if line.startswith(identifier + '\t'):
            results.append(TagResult.from_line(line))
    return results


def search(args):
    results = search_ctags(args.codebase, args.identifier)
    for result in results:
        print(result.pprint())


def register(subparsers: argparse.ArgumentParser):
    parser = subparsers.add_parser('ctags')
    parser.add_argument('codebase')
    parser.add_argument('identifier')
    parser.set_defaults(func=search)
