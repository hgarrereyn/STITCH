from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from typing import List
from .data import FunctionImpl, VarArg, FunctionImplError

console = Console()

def print_function_impl(function: FunctionImpl, index: int = None):
    """Pretty print a single function implementation with syntax highlighting."""
    
    # Create a title for the function
    title = f"Function: {function.name}"
    if index is not None:
        title = f"[{index}] {title}"
    
    # Create input/output tables
    inputs_table = Table(title="Inputs", show_header=True, header_style="bold blue")
    inputs_table.add_column("Name", style="cyan")
    inputs_table.add_column("Type", style="magenta")
    
    for var in function.inputs:
        inputs_table.add_row(var.name, var.type)
    
    outputs_table = Table(title="Outputs", show_header=True, header_style="bold green")
    outputs_table.add_column("Name", style="cyan")
    outputs_table.add_column("Type", style="magenta")
    
    for var in function.outputs:
        outputs_table.add_row(var.name, var.type)
    
    # Create syntax highlighted code
    syntax = Syntax(function.code, "c", theme="monokai", line_numbers=True)
    
    # Print everything
    console.print(f"\n[bold yellow]{title}[/bold yellow]")
    console.print(inputs_table)
    console.print(outputs_table)
    console.print(Panel(syntax, title="Implementation", border_style="blue"))

def print_functions(functions: List[FunctionImpl]):
    """Pretty print a list of function implementations."""
    
    if not functions:
        console.print("[red]No functions to display[/red]")
        return
    
    console.print(f"[bold green]Synthesized {len(functions)} functions:[/bold green]")
    
    for i, function in enumerate(functions, 1):
        print_function_impl(function, i)
    
    console.print()  # Add some spacing at the end

def pretty_print_error(error: FunctionImplError):
    console.print(f"[bold red]Error for {error.stub.name}:[/bold red]")
    
    syntax = Syntax(error.stub.code, "c", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title="Implementation", border_style="red"))

    syntax = Syntax(error.error, "text", theme="monokai", line_numbers=False)
    console.print(Panel(syntax, title="Error", border_style="red"))
    console.print()
