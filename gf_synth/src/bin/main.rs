use clap::Parser;
use gf_synth::{harness_writer::HarnessWriter, spec::Spec};



#[derive(Parser)]
#[command(author, version, about, long_about = None)]
pub struct Options {
    pub spec: String,
}

fn main() {
    let opts = Options::parse();
    println!("Parsing {}", opts.spec);

    // spec is a json file, read into Spec with serde_json
    let data = std::fs::read_to_string(&opts.spec).expect("Could not read spec file");
    let spec: Spec = serde_json::from_str(&data).expect("Could not parse spec");

    println!("Found {} types, {} endpoints, {} headers", spec.types.len(), spec.endpoints.len(), spec.headers.len());

    let writer = HarnessWriter::new(&spec);
    let code = writer.write_harness();
    println!("{}", code);
}
