
use serde::{Deserialize, Serialize};

// Internal spec format
#[derive(Debug, Serialize, Deserialize)]
pub struct Spec {
    pub types: Vec<TypeSpec>,
    pub endpoints: Vec<EndpointSpec>,
    pub headers: Vec<HeaderSpec>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TypeSpec {
    pub name: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct EndpointSpec {
    pub name: String,
    pub inputs: Vec<usize>,
    pub outputs: Vec<usize>,
    pub args: Vec<String>,
    pub template: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub enum HeaderSpec {
    CHeader(String),
    CppHeader(String),
}
