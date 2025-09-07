use std::env;
use std::path::PathBuf;

fn main() {
    let tool = cc::Build::new().get_compiler();
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());
    let out_dir = out_dir.join("../../../");

    println!("cargo:warning=>>> OUT_DIR: {}", out_dir.display());
    let mut cmd = tool.to_command();
    cmd.arg("-shared")
        .arg("-fPIC")
        .arg("-o")
        .arg(out_dir.join("liboom_wrapper.so"))
        .arg("oom_wrapper.c");
    let output = cmd.output().unwrap();
    println!("cargo:warning=>>> OUTPUT: {}", String::from_utf8_lossy(&output.stdout));
    println!("cargo:warning=>>> ERROR: {}", String::from_utf8_lossy(&output.stderr));
}
