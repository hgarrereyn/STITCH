use gf_core::harness::{Harness, InvokeResult};
use gf_core::mutation::MutationContext;
use gf_core::pspec::PSpec;
use std::path::PathBuf;
use std::rc::Rc;
use std::time::Duration;
use std::{env, fs};
use clap::{Args, Parser};
use components::graph_input::GraphInput;
use libafl::inputs::Input;

#[allow(unused_imports)]
use libafl_targets::{libfuzzer_initialize, libfuzzer_test_one_input, std_edges_map_observer};

use mimalloc::MiMalloc;
#[global_allocator]
static GLOBAL: MiMalloc = MiMalloc;

pub mod components;
pub mod fuzzer;
pub mod minimizer;
pub mod seccomp;

#[derive(Parser)]
#[command(author, version, about, long_about = None)]
pub struct Cli {
    /// Positional arguments that can appear before or after named arguments
    pub args: Vec<String>,

    #[command(flatten)]
    pub options: Options,
}

#[derive(Args)]
pub struct Options {
    /// Path to output directory, will contain `/queue` and `/crashes` subdirectories
    #[arg(short, long)]
    pub out: Option<String>,

    /// Path to input directory, contains graph seeds
    #[arg(short, long)]
    pub input: Option<String>,

    /// Path to directory containing raw input files, used for hint mutations
    #[arg(short, long)]
    pub raw_input: Option<String>,

    #[arg(short, long, default_value = "false")]
    pub bypass_validation: bool,

    #[arg(short, long, default_value = "10")]
    pub compute_size: usize,

    /// If provided, execute the specified testcase instead of fuzzing.
    #[arg(short, long)]
    pub execute: Option<String>,

    /// If provided, convert the specified testcase to source code.
    #[arg(short, long)]
    pub write: Option<String>,

    /// If provided, minimize the specified testcase.
    #[arg(short, long)]
    pub minimize: Option<String>,

    /// Output path for minimized testcase.
    #[arg(short, long)]
    pub minimize_output: Option<String>,

    /// If provided, write the full testcase code during minimization.
    #[arg(short, long, default_value = "false")]
    pub full_write: bool,

    /// Number of cycles to run during minimization.
    #[arg(short, long, default_value = "10000")]
    pub minimize_cycles: usize,

    /// Number of seconds to run during minimization.
    #[arg(short, long, default_value = "10")]
    pub minimize_timeout: usize,

    /// Path to pspec file (by default assumed to be {fuzzer}.pspec)
    #[arg(short, long)]
    pub pspec: Option<String>,

    /// Path to tokens file
    #[arg(short, long)]
    pub tokens: Option<String>,

    /// Path to logfile
    #[arg(short, long, default_value = "libafl.log")]
    pub logfile: String,

    #[arg(short, long, default_value = "1200")]
    pub timeout: String,

    #[arg(short, long, default_value = "2000")]
    pub rss_limit_mb: u64,

    #[arg(short, long, default_value = "false")]
    pub keep_stdout: bool,

    #[arg(short, long, default_value = "false")]
    pub verbose: bool,
}

fn compute_mutation_context(pspec: PSpec, pspec_path: String, compute_size: usize, bypass_validation: bool) -> Rc<MutationContext> {
    let pspec_cache_path = PathBuf::from(format!("{}.cache", pspec_path));
    let mctx = MutationContext::new(pspec);
    if pspec_cache_path.exists() {
        println!("Loading pspec cache from {:?}", pspec_cache_path);
        mctx.load(&pspec_cache_path).expect("Could not load pspec cache");
    } else {
        println!("Computing mutation context...");
        mctx.compute(compute_size, bypass_validation);
        println!("Computed mutation context");
        mctx.save(&pspec_cache_path).expect("Could not save pspec cache");
    }
    Rc::new(mctx)
}

#[no_mangle]
pub extern "C" fn libafl_main() {
    let res = Cli::parse().options;

    let gf_harness = Harness::new();

    // pspec is either {fuzzer}.pspec or passed in as a command line argument
    let pspec_path = res.pspec.clone().unwrap_or_else(|| {
        let mut p = PathBuf::from(env::current_exe().unwrap());
        p.set_extension("pspec");
        p.to_string_lossy().to_string()
    });

    let pspec = fs::read_to_string(&pspec_path).expect("Could not read pspec file");
    let mut pspec: PSpec = serde_json::from_str(&pspec).expect("Could not parse pspec");

    for (i, s) in gf_harness.shim_sizes.iter().enumerate() {
        pspec.endpoints[i].context_size = Some(*s as usize);
    }

    for (i, s) in gf_harness.shim_arg_offsets.iter().enumerate() {
        pspec.endpoints[i].arg_offsets = s.clone();
    }

    // Compute arg sizes based on arg_offsets
    for (i, s) in gf_harness.shim_arg_offsets.iter().enumerate() {
        let mut arg_sizes = vec![];
        for j in 1..s.len() {
            arg_sizes.push(s[j] - s[j-1]);
        }
        if s.len() > 0 {
            arg_sizes.push(pspec.endpoints[i].context_size.unwrap() as u32 - s[s.len() - 1]);
        }
        pspec.endpoints[i].arg_sizes = arg_sizes;
    }

    if res.input.is_some() && res.out.is_some() {
        // Fuzzing!
        let ctx = compute_mutation_context(pspec, pspec_path, res.compute_size, res.bypass_validation);        
        run_fuzzer(res, gf_harness, ctx);
    } else if res.execute.is_some() {
        // Execute one testcase
        execute_one(res, gf_harness);
    } else if res.write.is_some() {
        // Write one testcase
        write_one(res, gf_harness);
    } else if res.minimize.is_some() {
        // Minimize one testcase
        let ctx = compute_mutation_context(pspec, pspec_path, res.compute_size, res.bypass_validation);
        minimizer::minimize(res.minimize.unwrap(), res.minimize_output, res.minimize_cycles, res.minimize_timeout, gf_harness, res.full_write, ctx);
    } else {
        // No action specified

        // Compute mutation context to force the cache to be created.
        // (downstream users can run the harness with no arguments to force cache creation)
        let _ = compute_mutation_context(pspec, pspec_path, res.compute_size, res.bypass_validation);

        println!("Either input and out directories, execute command, test-filter with input, or minimize must be provided.");
        return;
    }
}

fn run_fuzzer(res: Options, gf_harness: Harness, ctx: Rc<MutationContext>) {
    println!(
        "Workdir: {:?}",
        env::current_dir().unwrap().to_string_lossy().to_string()
    );

    // For fuzzbench, crashes and finds are inside the same `corpus` directory, in the "queue" and "crashes" subdir.
    let out_dir = PathBuf::from(res.out.unwrap());
    if fs::create_dir(&out_dir).is_err() {
        println!("Out dir at {:?} already exists.", &out_dir);
        if !out_dir.is_dir() {
            println!("Out dir at {:?} is not a valid directory!", &out_dir);
            return;
        }
    }

    let in_dir = PathBuf::from(res.input.unwrap());
    if !in_dir.is_dir() {
        println!("In dir at {:?} is not a valid directory!", &in_dir);
        return;
    }

    let raw_in_dir = res.raw_input.map(PathBuf::from);

    let tokens = res.tokens.map(PathBuf::from);

    let logfile = PathBuf::from(res.logfile);

    let timeout = Duration::from_millis(
        res.timeout
            .parse()
            .expect("Could not parse timeout in milliseconds"),
    );

    fuzzer::fuzz(
        gf_harness, ctx, &out_dir, &in_dir, raw_in_dir,tokens,
        &logfile, timeout, Some(res.rss_limit_mb), res.keep_stdout, res.verbose
    ).unwrap();
}

fn execute_one(res: Options, gf_harness: Harness) {
    let path = PathBuf::from(res.execute.unwrap());
    let inp = GraphInput::from_file(path).expect("Could not read input file");

    let graph = inp.graph;

    println!("Executing graph:");
    graph.pprint();

    let res = gf_harness.invoke(&graph, None);
    println!("Execution result: {:?}", res);

    match res {
        InvokeResult::Success => {},
        InvokeResult::Bailed(_) => {},
        InvokeResult::AssertionViolation(_, msg) => {
            // Raise an abort with the message
            unsafe {
                panic!("FUZZ_ASSERT() violation: {}", msg);
            }
        },
        InvokeResult::LibraryException(_, _) => {},
    }
}

fn write_one(res: Options, gf_harness: Harness) {
    let path = PathBuf::from(res.write.unwrap());
    let inp = GraphInput::from_file(path).expect("Could not read input file");

    let graph = inp.graph;

    println!("\n-----BEGIN GRAPH WRITE-----");
    gf_harness.write(&graph);
    println!("-----END GRAPH WRITE-----");
}
