use clap::Parser;
use gf_synth::{harness_writer::HarnessWriter, schema_writer::SchemaWriter, spec::{Spec, Config}};



#[derive(Parser)]
#[command(author, version, about, long_about = None)]
pub struct Options {
    pub spec: String,

    #[arg(short, long)]
    pub config: Option<String>,

    #[arg(short, long)]
    pub out: Option<String>,

    #[arg(short, long)]
    pub schema: Option<String>,

    #[arg(short, long)]
    pub invalid_types: Option<String>,
}

fn main() {
    let opts = Options::parse();
    println!("Parsing {}", opts.spec);

    // spec is a json file, read into Spec with serde_json
    let data = std::fs::read_to_string(&opts.spec).expect("Could not read spec file");
    let spec: Spec = serde_json::from_str(&data).expect("Could not parse spec");

    // Load config if provided
    let config = if let Some(config_file) = opts.config {
        let config_data = std::fs::read_to_string(&config_file).expect("Could not read config file");
        serde_json::from_str::<Config>(&config_data).expect("Could not parse config")
    } else {
        Config::default()
    };

    // Load banned types if provided
    let banned_types = if let Some(banned_file) = opts.invalid_types {
        let banned_data = std::fs::read_to_string(&banned_file).expect("Could not read banned types file");
        serde_json::from_str::<Vec<String>>(&banned_data).expect("Could not parse banned types")
    } else {
        Vec::new()
    };

    if let Err(errors) = spec.validate(&banned_types) {
        eprintln!("{}", errors);
        std::process::exit(1);
    }

    let inner = spec.generate(&config);

    println!("Found {} types, {} endpoints", inner.types.len(), inner.endpoints.len());

    if let Some(harness) = opts.out {
        println!("Writing harness to {}", harness);

        let writer = HarnessWriter::new(&inner, &config);
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
