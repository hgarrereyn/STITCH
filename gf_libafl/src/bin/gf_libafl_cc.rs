use std::env;

use libafl_cc::{ClangWrapper, CompilerWrapper, ToolWrapper, LLVMPasses};

pub fn main() {
    let mut args: Vec<String> = env::args().collect();
    if args.len() > 1 {
        let mut dir = env::current_exe().unwrap();
        let wrapper_name = dir.file_name().unwrap().to_str().unwrap();

        let is_cpp = match wrapper_name[wrapper_name.len()-2..].to_lowercase().as_str() {
            "cc" => false,
            "++" | "pp" | "xx" => true,
            _ => panic!("Could not figure out if c or c++ wrapper was called. Expected {dir:?} to end with c or cxx"),
        };

        dir.pop();

        // Must be always present, even without --libafl
        args.push("-fsanitize-coverage=trace-pc-guard,trace-cmp".into());

        let use_asan = env::var("GRAPHFUZZ_USE_ASAN").unwrap_or("0".to_string());
        if use_asan == "1" {
            args.push("-fsanitize=address".into());
        }

        // Get the GRAPHFUZZ_BUILD_COVERAGE environment variable
        let build_coverage = env::var("GRAPHFUZZ_BUILD_COVERAGE").unwrap_or("0".to_string());
        if build_coverage == "1" {
            args.push("-fprofile-instr-generate".into());
            args.push("-fcoverage-mapping".into());
            args.push("-gline-tables-only".into());
        }

        let mut cc = ClangWrapper::new();

        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        cc.add_pass(LLVMPasses::AutoTokens);

        if let Some(code) = cc
            .cpp(is_cpp)
            // silence the compiler wrapper output, needed for some configure scripts.
            .silence(true)
            // add arguments only if --libafl or --libafl-no-link are present
            .need_libafl_arg(true)
            .parse_args(&args)
            .expect("Failed to parse the command line")
            .link_staticlib(&dir, "gf_libafl")
            .add_pass(LLVMPasses::CmpLogRtn)
            // .add_args(&[
            //     format!("-L{}", dir.display()),
            //     "-loom_wrapper".to_string(),
            //     format!("-Wl,-rpath,{}", dir.display()),
            // ])
            .add_args(&[
                "-lseccomp",
            ])
            .run()
            .expect("Failed to run the wrapped compiler")
        {
            std::process::exit(code);
        }
    } else {
        panic!("GraphFuzz AFL CC: No Arguments given");
    }
}
