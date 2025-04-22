use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
pub struct ProjectInfo {
    pub name: String,
    pub codebase: String,
    pub build: BuildInfo,
    pub codegen: CodegenInfo,
}

#[derive(Serialize, Deserialize)]
pub struct BuildInfo {
    pub include_paths: Vec<String>,
    pub lib_paths: Vec<String>,
    pub libs: Vec<String>,
    pub source_files: Vec<String>,
}

#[derive(Serialize, Deserialize)]
pub struct CodegenInfo {
    pub headers: Vec<CodegenHeader>,
    pub defines: Option<Vec<CodegenDefine>>,
}

#[derive(Serialize, Deserialize)]
pub struct CodegenHeader {
    pub name: String,
    pub language: String,
}

#[derive(Serialize, Deserialize)]
pub struct CodegenDefine {
    pub name: String,
    pub value: String,
}
