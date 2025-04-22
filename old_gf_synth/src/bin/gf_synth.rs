use clap::Parser;
use gf_synth::{harness_writer::HarnessWriter, schema_writer::SchemaWriter, spec::Spec, info::ProjectInfo};


#[derive(Parser)]
#[command(author, version, about, long_about = None)]
pub struct Options {
    pub spec: String,

    #[arg(short, long, required = true)]
    pub info: Option<String>,

    #[arg(short, long)]
    pub out: Option<String>,

    #[arg(short, long)]
    pub schema: Option<String>,
}

fn main() {
    let opts = Options::parse();
    println!("Parsing {}", opts.spec);

    // Load spec
    let data = std::fs::read_to_string(&opts.spec).expect("Could not read spec file");
    let spec: Spec = serde_json::from_str(&data).expect("Could not parse spec");

    // Load project info
    let info_data = std::fs::read_to_string(&opts.info.unwrap()).expect("Could not read info file");
    let info: ProjectInfo = serde_json::from_str(&info_data).expect("Could not parse info");

    // Generate inner spec
    let inner = spec.generate();

    println!("Found {} types, {} endpoints", inner.types.len(), inner.endpoints.len());

    if let Some(harness) = opts.out {
        println!("Writing harness to {}", harness);

        let writer = HarnessWriter::new(&inner, &info);
        let code = writer.write_harness();

        std::fs::write(harness, code).expect("Could not write harness file");
    }
    
    if let Some(schema) = opts.schema {
        println!("Writing schema to {}", schema);

        let writer = SchemaWriter::new(&inner);
        let code = writer.write_schema();

        std::fs::write(schema, code).expect("Could not write schema file");
    }
}
