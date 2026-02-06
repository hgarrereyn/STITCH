from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from typing import List

console = Console()

def pretty_print_code(code: str, language: str = "python", theme: str = "monokai", line_numbers: bool = True, title: str = None):
    syntax = Syntax(code, language, theme=theme, line_numbers=line_numbers, word_wrap=True)
    console.print(Panel(syntax, title=title, border_style="blue"))
