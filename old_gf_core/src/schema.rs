use serde::{Serialize, Deserialize};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Type {
    pub name: String,
    pub idx: usize,
    // TODO: hierarchies
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct VariableMapping {
    pub name: String,
    pub in_idx: Option<usize>,
    pub out_idx: Option<usize>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Endpoint {
    pub name: String,
    pub idx: usize,
    pub inputs: Vec<usize>,
    pub outputs: Vec<usize>,
    pub context_size: Option<usize>,
    pub template: Option<String>,
    pub mappings: Vec<VariableMapping>,
}

// The schema is produced during harness generation and stored as `{/path/to/fuzzer}.schema` in JSON format
#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Schema {
    pub types: Vec<Type>,
    pub endpoints: Vec<Endpoint>,
}

impl Default for Schema {
    fn default() -> Self {
        Self {
            types: vec![Type {
                name: "default".to_string(),
                idx: 0,
            }],
            endpoints: vec![Endpoint {
                name: "default".to_string(),
                idx: 0,
                inputs: vec![],
                outputs: vec![],
                context_size: Some(0),
                template: None,
                mappings: vec![],
            }],
        }
    }
}
