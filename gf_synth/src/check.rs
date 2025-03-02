use tempfile;
use std::io::Write;

use crate::spec::HeaderSpec;


const CLANG: &str = "clang++";
const CHECK_PROG: &str = include_str!("./type_check.cpp");

fn format_header(header: &str) -> String {
    // check if system or user header
    if header.contains(".") {
        format!("#include \"{}\"", header)
    } else {
        format!("#include <{}>", header)
    }
}

pub fn is_primitive_constructable(typ: &str, headers: &[HeaderSpec]) -> Result<bool, ()> {
    let prog = CHECK_PROG.replace("TYPE_HERE", typ);

    let mut code = String::new();
    for header in headers {
        match header {
            HeaderSpec::CHeader(header) => {
                code.push_str("extern \"C\" {\n");
                code.push_str(&format!("    {}\n", format_header(header)));
                code.push_str("}\n");
            }
            HeaderSpec::CppHeader(header) => {
                code.push_str(&format!("{}\n", format_header(header)));
            }
        }
    }

    code.push_str(&prog);

    // Write the program to a temporary file ending in .cpp
    let mut source_file = tempfile::NamedTempFile::new().map_err(|_| ())?;
    source_file.write_all(code.as_bytes()).map_err(|_| ())?;

    // Create output path but don't keep the file handle open
    let output_path = tempfile::NamedTempFile::new()
        .map_err(|_| ())?
        .into_temp_path();

    // Compile the program
    let status = std::process::Command::new(CLANG)
        .arg("-x")
        .arg("c++")
        .arg("-std=c++17")
        .arg("-I.")
        .arg("-o")
        .arg(&output_path)
        .arg(source_file.path())
        .status()
        .map_err(|_| ())?;

    if !status.success() {
        println!("Compilation failed");
        return Err(());
    }

    // Run the program
    let status = std::process::Command::new(&output_path)
        .status()
        .map_err(|_| ())?;

    Ok(status.success())
}
