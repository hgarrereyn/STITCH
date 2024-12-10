
use core::graph::{Graph, Node, NodeRef};
use core::harness::Harness;
use core::schema::Schema;
use std::path::PathBuf;
use std::time::Duration;
use std::{env, fs};
use clap::{Args, Parser};
use components::graph_input::GraphInput;
use libafl::inputs::Input;
use libafl::observers::StdMapObserver;
use libafl_targets::{libfuzzer_initialize, libfuzzer_test_one_input, std_edges_map_observer};

pub mod components;
pub mod core;
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
    pub execute: Option<String>,

    #[arg(short, long)]
    pub minimize: Option<String>,

    #[arg(short, long)]
    pub schema: Option<String>,

    #[arg(short, long)]
    pub tokens: Option<String>,

    #[arg(short, long, default_value = "libafl.log")]
    pub logfile: String,

    #[arg(short, long, default_value = "1200")]
    pub timeout: String,
}

#[no_mangle]
pub extern "C" fn libafl_main() {
    let res = Cli::parse().options;
    let obs = unsafe { std_edges_map_observer("edges") };

    let gf_harness = Harness::new();

    // schema is either {fuzzer}.schema or passed in as a command line argument
    let schema_path = res.schema.clone().unwrap_or_else(|| {
        let mut p = PathBuf::from(env::current_exe().unwrap());
        p.set_extension("schema");
        p.to_string_lossy().to_string()
    });

    println!("Using schema at {:?}", schema_path);

    let schema = fs::read_to_string(schema_path).expect("Could not read schema file");
    let schema: Schema = serde_json::from_str(&schema).expect("Could not parse schema");

    if res.input.is_some() && res.out.is_some() {
        run_fuzzer(res, gf_harness, schema, obs);
    } else if res.execute.is_some() {
        execute_one(res, gf_harness);
    } else if res.minimize.is_some() {
        minimizer::minimize(res.minimize.unwrap(), gf_harness, schema);
    } else {
        println!("Either input and out directories or execute command must be provided.");
        return;
    }
}

fn run_fuzzer(res: Options, gf_harness: Harness, schema: Schema, obs: StdMapObserver<u8,false>) {
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

    fuzzer::fuzz(
        gf_harness, schema, obs, out_dir, crashes, &in_dir, tokens, &logfile, timeout
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
