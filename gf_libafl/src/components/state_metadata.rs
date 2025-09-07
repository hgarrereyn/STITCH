use libafl_bolts::{impl_serdeany, Named};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum StateMode {
    Initializing,
    AddingSeed(usize),
    Running,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMetadata {
    pub mode: StateMode,
}

impl StateMetadata {
    pub fn new() -> Self {
        Self { mode: StateMode::Initializing }
    }
}

impl_serdeany!(StateMetadata);
