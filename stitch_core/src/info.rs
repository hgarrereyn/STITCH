use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
pub struct ProjectInfo {
    pub build_flags: Vec<String>,
    pub codegen: CodegenInfo,
}

#[derive(Serialize, Deserialize)]
pub struct CodegenInfo {
    pub headers: Vec<CodegenHeader>,
    pub defines: Option<Vec<CodegenDefine>>,
}

#[derive(Serialize, Deserialize)]
pub struct CodegenDefine {
    pub name: String,
    pub value: String,
}

#[derive(Serialize, Deserialize)]
pub struct CodegenHeader {
    pub path: String,
    pub language: String,
}
