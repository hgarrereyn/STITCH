
use serde::{Serialize, Deserialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct Type {
    pub name: String,
    pub idx: usize,
    // TODO: hierarchies
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Endpoint {
    pub name: String,
    pub idx: usize,
    pub inputs: Vec<usize>,
    pub outputs: Vec<usize>,
    pub context_size: usize,
}

// The schema is produced during harness generation and stored as `{/path/to/fuzzer}.schema` in JSON format
#[derive(Debug, Serialize, Deserialize)]
pub struct Schema {
    pub types: Vec<Type>,
    pub endpoints: Vec<Endpoint>,
}
