
use gf_core::schema::VariableMapping;
use serde::{Deserialize, Serialize};


#[derive(Debug, Serialize, Deserialize)]
pub struct InnerSpec {
    pub types: Vec<InnerTypeSpec>,
    pub endpoints: Vec<InnerEndpointSpec>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub struct InnerTypeSpec {
    pub name: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct InnerEndpointSpec {
    pub name: String,
    pub inputs: Vec<usize>,
    pub outputs: Vec<usize>,
    pub args: Vec<String>,
    pub template: String,

    pub mappings: Vec<VariableMapping>,
}
