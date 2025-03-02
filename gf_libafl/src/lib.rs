use gf_core::filter::Filter;
use gf_core::graph::Graph;
use gf_core::harness::Harness;
use gf_core::mutation::MutationContext;
use gf_core::schema::Schema;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Duration;
use std::{env, fs};
use clap::{Args, Parser};
use components::graph_input::GraphInput;
use libafl::inputs::Input;
use libafl::observers::StdMapObserver;
use libafl_targets::{libfuzzer_initialize, libfuzzer_test_one_input, std_edges_map_observer};
use pyo3::prelude::*;

pub mod components;
pub mod fuzzer;
pub mod minimizer;

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
    pub schema: Option<String>,

    #[arg(short, long)]
    pub tokens: Option<String>,

    #[arg(short, long, default_value = "libafl.log")]
    pub logfile: String,

    #[arg(short, long, default_value = "1200")]
    pub timeout: String,

    #[arg(long)]
    pub test_filter: Option<String>,

    #[arg(long)]
    pub test_filter_input: Option<String>,

    #[arg(long)]
    pub to_py_str_repr: Option<String>,
}

#[no_mangle]
pub extern "C" fn libafl_main() {
    let res = Cli::parse().options;

    let gf_harness = Harness::new();

    // schema is either {fuzzer}.schema or passed in as a command line argument
    let schema_path = res.schema.clone().unwrap_or_else(|| {
        let mut p = PathBuf::from(env::current_exe().unwrap());
        p.set_extension("schema");
        p.to_string_lossy().to_string()
    });

    let schema = fs::read_to_string(&schema_path).expect("Could not read schema file");
    let mut schema: Schema = serde_json::from_str(&schema).expect("Could not parse schema");

    for (i, s) in gf_harness.shim_sizes.iter().enumerate() {
        schema.endpoints[i].context_size = Some(*s as usize);
    }

    if res.test_filter.is_some() && res.test_filter_input.is_some() {
        test_filter(res, schema.clone());
    } else if res.to_py_str_repr.is_some() {
        convert_graph_to_py_str_repr(res, schema.clone());
    } else {
        // Perform schema computation and caching/loading
        let schema_cache_path = PathBuf::from(format!("{}.cache", schema_path));
        let mctx = MutationContext::new(schema);
        if schema_cache_path.exists() {
            println!("Loading schema cache from {:?}", schema_cache_path);
            mctx.load(&schema_cache_path).expect("Could not load schema cache");
        } else {
            println!("Computing mutation context...");
            mctx.compute(res.compute_size, res.bypass_validation);
            println!("Computed mutation context");
            mctx.save(&schema_cache_path).expect("Could not save schema cache");
        }
        let ctx = Rc::new(mctx);

        if res.input.is_some() && res.out.is_some() {
            run_fuzzer(res, gf_harness, ctx);
        } else if res.execute.is_some() {
            execute_one(res, gf_harness);
        } else if res.write.is_some() {
            write_one(res, gf_harness);
        } else if res.minimize.is_some() {
            minimizer::minimize(res.minimize.unwrap(), res.minimize_output, res.minimize_cycles, gf_harness, res.full_write, ctx);
        } else {
            println!("Either input and out directories, execute command, test-filter with input, or minimize must be provided.");
            return;
        }
    }
}

fn run_fuzzer(res: Options, gf_harness: Harness, ctx: Rc<MutationContext>) {
    println!(
        "Workdir: {:?}",
        env::current_dir().unwrap().to_string_lossy().to_string()
    );

    // For fuzzbench, crashes and finds are inside the same `corpus` directory, in the "queue" and "crashes" subdir.
    let mut out_dir = PathBuf::from(res.out.unwrap());
    if fs::create_dir(&out_dir).is_err() {
        println!("Out dir at {:?} already exists.", &out_dir);
        if !out_dir.is_dir() {
            println!("Out dir at {:?} is not a valid directory!", &out_dir);
            return;
        }
    }
    let mut crashes = out_dir.clone();
    crashes.push("crashes");
    out_dir.push("queue");

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

    let filter_dir = res.filter_dir.map(PathBuf::from);

    fuzzer::fuzz(
        gf_harness, ctx, out_dir, crashes, &in_dir, tokens,
        &logfile, timeout, filter_dir
    ).unwrap();
}

fn execute_one(res: Options, gf_harness: Harness) {
    let path = PathBuf::from(res.execute.unwrap());
    let inp = GraphInput::from_file(path).expect("Could not read input file");

    let graph = inp.graph;

    println!("Executing graph:");
    graph.pprint();

    gf_harness.invoke(&graph);
}

fn write_one(res: Options, gf_harness: Harness) {
    let path = PathBuf::from(res.write.unwrap());
    let inp = GraphInput::from_file(path).expect("Could not read input file");

    let graph = inp.graph;

    println!("\n-----BEGIN GRAPH WRITE-----");
    gf_harness.write(&graph);
    println!("-----END GRAPH WRITE-----");
}

fn test_filter(res: Options, schema: Schema) {
    pyo3::prepare_freethreaded_python();
    Python::with_gil(|py| {
        let filter_path = Path::new(res.test_filter.as_ref().unwrap());
        let input_path = Path::new(res.test_filter_input.as_ref().unwrap());
        
        let filter = Filter::new(filter_path).expect("Could not create filter");
        let inp = GraphInput::from_file(input_path).expect("Could not read input file");
        
        let result = filter.test(py, &inp.graph, &schema);
        if let Err(e) = result {
            let err = e.downcast::<PyErr>().unwrap();
            err.print(py);
        } else {
            let res = result.unwrap();
            println!("{}", if res { 1 } else { 0 });
        }
    });
}

fn convert_graph_to_py_str_repr(res: Options, schema: Schema) {
    let path = PathBuf::from(res.to_py_str_repr.unwrap());
    let inp = GraphInput::from_file(path).expect("Could not read input file");
    let graph = inp.graph;
    
    pyo3::prepare_freethreaded_python();
    let result: Result<String, PyErr> = Python::with_gil(|py| {
        let graph = graph.to_py_str_repr(py, &schema).expect("Could not convert graph to Python string representation");
        Ok(graph)
    });
    println!("{}", result.unwrap());
}
