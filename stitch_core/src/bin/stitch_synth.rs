use clap::Parser;
use stitch_core::{writer::harness_writer::HarnessWriter, spec::Spec, info::ProjectInfo};


#[derive(Parser)]
#[command(author, version, about, long_about = None)]
pub struct Options {
    pub spec: String,

    #[arg(short, long, required = true)]
    pub info: Option<String>,

    #[arg(short, long)]
    pub out: Option<String>,

    #[arg(short, long)]
    pub pspec: Option<String>,
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
    let res = spec.build_pspec();

    if let Err(errors) = res {
        for error in errors {
            eprintln!("---------------------\n{}", error);
        }
        return;
    }

    let pspec = res.unwrap();

    println!("Found {} types, {} endpoints", pspec.objects.len(), pspec.endpoints.len());

    if let Some(harness) = opts.out {
        println!("Writing harness to {}", harness);

        let writer = HarnessWriter::new(&pspec, &info);
        let code = writer.write_harness();

        std::fs::write(harness, code).expect("Could not write harness file");
    }
    
    if let Some(pspec_path) = opts.pspec {
        println!("Writing pspec to {}", pspec_path);

        // JSON serialize the pspec
        let pspec_json = serde_json::to_string_pretty(&pspec).expect("Could not serialize pspec");

        std::fs::write(pspec_path, pspec_json).expect("Could not write pspec file");
    }
}
