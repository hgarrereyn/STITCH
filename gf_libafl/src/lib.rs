use gf_core::harness::Harness;
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

// use crate::components::runtime_metadata::FuzzerRuntimeMetadata;

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
    #[arg(short, long)]
    pub out: Option<String>,

    #[arg(short, long)]
    pub input: Option<String>,

    #[arg(short, long)]
    pub filter_dir: Option<String>,

    #[arg(short, long, default_value = "false")]
    pub bypass_validation: bool,

    #[arg(short, long, default_value = "10")]
    pub compute_size: usize,

    #[arg(short, long)]
    pub execute: Option<String>,

    #[arg(short, long)]
    pub write: Option<String>,

    #[arg(short, long)]
    pub minimize: Option<String>,

    #[arg(short, long)]
    pub minimize_output: Option<String>,

    #[arg(short, long, default_value = "false")]
    pub full_write: bool,

    #[arg(short, long, default_value = "10000")]
    pub minimize_cycles: usize,

    #[arg(short, long)]
    pub pspec: Option<String>,

    #[arg(short, long)]
    pub tokens: Option<String>,

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
        pspec.endpoints[i].hint_arg_offsets = s.clone();
    }

    if res.input.is_some() && res.out.is_some() {
        // Perform schema computation and caching/loading
        let pspec_cache_path = PathBuf::from(format!("{}.cache", pspec_path));
        let mctx = MutationContext::new(pspec);
        if pspec_cache_path.exists() {
            println!("Loading pspec cache from {:?}", pspec_cache_path);
            mctx.load(&pspec_cache_path).expect("Could not load pspec cache");
        } else {
            println!("Computing mutation context...");
            mctx.compute(res.compute_size, res.bypass_validation);
            println!("Computed mutation context");
            mctx.save(&pspec_cache_path).expect("Could not save pspec cache");
        }
        let ctx = Rc::new(mctx);
        
        run_fuzzer(res, gf_harness, ctx);
    } else if res.execute.is_some() {
        execute_one(res, gf_harness);
    } else if res.write.is_some() {
        write_one(res, gf_harness);
    } else if res.minimize.is_some() {
        // Perform schema computation and caching/loading
        let pspec_cache_path = PathBuf::from(format!("{}.cache", pspec_path));
        let mctx = MutationContext::new(pspec);
        if pspec_cache_path.exists() {
            println!("Loading pspec cache from {:?}", pspec_cache_path);
            mctx.load(&pspec_cache_path).expect("Could not load pspec cache");
        } else {
            println!("Computing mutation context...");
            mctx.compute(res.compute_size, res.bypass_validation);
            println!("Computed mutation context");
            mctx.save(&pspec_cache_path).expect("Could not save pspec cache");
        }
        let ctx = Rc::new(mctx);
        
        minimizer::minimize(res.minimize.unwrap(), res.minimize_output, res.minimize_cycles, gf_harness, res.full_write, ctx);
    } else {
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

    let tokens = res.tokens.map(PathBuf::from);

    let logfile = PathBuf::from(res.logfile);

    let timeout = Duration::from_millis(
        res.timeout
            .parse()
            .expect("Could not parse timeout in milliseconds"),
    );

    fuzzer::fuzz(
        gf_harness, ctx, &out_dir, &in_dir, tokens,
        &logfile, timeout, Some(res.rss_limit_mb), res.keep_stdout, res.verbose
    ).unwrap();
}

fn execute_one(res: Options, gf_harness: Harness) {
    let path = PathBuf::from(res.execute.unwrap());
    let inp = GraphInput::from_file(path).expect("Could not read input file");

    let graph = inp.graph;

    println!("Executing graph:");
    graph.pprint();

    gf_harness.invoke(&graph, None);
}

fn write_one(res: Options, gf_harness: Harness) {
    let path = PathBuf::from(res.write.unwrap());
    let inp = GraphInput::from_file(path).expect("Could not read input file");

    let graph = inp.graph;

    println!("\n-----BEGIN GRAPH WRITE-----");
    gf_harness.write(&graph);
    println!("-----END GRAPH WRITE-----");
}
