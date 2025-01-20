import json
from typing import List, Optional, Dict, Set
import subprocess
import tempfile

from .colors import Colors
from .spec import Schema

def merge_schemas(inputs: List[str], output: str):
    merged_schema = {
        "endpoints": [],
        "headers": []
    }
    unique_headers = set()

    for schema_path in inputs:
        with open(schema_path, 'r') as schema_file:
            schema = json.load(schema_file)
            merged_schema["endpoints"].extend(schema.get("endpoints", []))
            
            for header in schema.get("headers", []):
                header_tuple = tuple(header.items())
                if header_tuple not in unique_headers:
                    unique_headers.add(header_tuple)
                    merged_schema["headers"].append(header)

    with open(output, 'w') as output_file:
        json.dump(merged_schema, output_file, indent=2)


def find_missing_constructors(schema: Dict) -> Set[str]:
    """Find types that don't have constructors in the schema."""
    types = {t["name"] for t in schema["types"]}
    
    # Look through endpoints for constructor-like functions
    constructed_types = set()
    for endpoint in schema["endpoints"]:
        # A constructor typically has no inputs of the type and outputs the type
        inputs = {schema["types"][idx]["name"] for idx in endpoint.get("inputs", [])}
        outputs = {schema["types"][idx]["name"] for idx in endpoint.get("outputs", [])}
        
        for typ in types:
            if typ not in inputs and typ in outputs:
                constructed_types.add(typ)
    
    return types - constructed_types


def find_missing_destructors(schema: Dict) -> Set[str]:
    """Find types that don't have destructors in the schema."""
    types = {t["name"] for t in schema["types"]}
    
    # Look through endpoints for destructor-like functions
    destroyed_types = set()
    for endpoint in schema["endpoints"]:
        # A destructor typically takes the type as input and has no outputs of that type
        inputs = {schema["types"][idx]["name"] for idx in endpoint.get("inputs", [])}
        outputs = {schema["types"][idx]["name"] for idx in endpoint.get("outputs", [])}
        
        for typ in types:
            if typ in inputs and typ not in outputs:
                destroyed_types.add(typ)
    
    return types - destroyed_types


def analyze_schema(schema_path: str) -> List[str]:
    """Analyze a schema file for types missing constructors/destructors.
    
    Args:
        schema_path: Path to the schema file to analyze
    """
    with open(schema_path) as f:
        schema = json.load(f)
    
    missing_constructors = find_missing_constructors(schema)
    missing_destructors = find_missing_destructors(schema)
    
    # Collect all invalid types
    invalid_types = sorted(missing_constructors | missing_destructors)
    
    return invalid_types


def find_missing_types(schema: Schema, config: str = None) -> List[str]:
    """Main function to process input schema and find missing constructors/destructors."""
    # Create temporary schema file
    with tempfile.TemporaryDirectory() as tempdir:
        temp_spec = tempdir + '/spec.json'
        with open(temp_spec, 'w') as f:
            f.write(schema.model_dump_json(indent=4))

        temp_schema = tempdir + '/schema'

        # Run gf_synth to generate schema
        try:
            cmd = ['gf_synth', temp_spec, '-s', temp_schema]
            if config:
                cmd += ['--config', config]
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            print(f"{Colors.LIGHT_RED}Error running gf_synth: {e}{Colors.END}")
            return
        except FileNotFoundError:
            print(f"{Colors.LIGHT_RED}Error: gf_synth not found in PATH{Colors.END}")
            return
        
        # Analyze the generated schema
        return analyze_schema(temp_schema)
