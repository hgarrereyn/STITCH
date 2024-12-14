use clap::Parser;
use gf_synth::{harness_writer::HarnessWriter, schema_writer::SchemaWriter, spec::Spec};



#[derive(Parser)]
#[command(author, version, about, long_about = None)]
pub struct Options {
    pub spec: String,

    #[arg(short, long)]
    pub out: Option<String>,

    #[arg(short, long)]
    pub schema: Option<String>,
}

fn main() {
    let opts = Options::parse();
    println!("Parsing {}", opts.spec);

    // spec is a json file, read into Spec with serde_json
    let data = std::fs::read_to_string(&opts.spec).expect("Could not read spec file");
    let spec: Spec = serde_json::from_str(&data).expect("Could not parse spec");

    println!("Found {} types, {} endpoints, {} headers", spec.types.len(), spec.endpoints.len(), spec.headers.len());

    if let Some(harness) = opts.out {
        println!("Writing harness to {}", harness);

        let writer = HarnessWriter::new(&spec);
        let code = writer.write_harness();

        std::fs::write(harness, code).expect("Could not write harness file");
    }
    
    if let Some(schema) = opts.schema {
        println!("Writing schema to {}", schema);

        let writer = SchemaWriter::new(&spec);
        let code = writer.write_schema();

        std::fs::write(schema, code).expect("Could not write schema file");
    }
}
