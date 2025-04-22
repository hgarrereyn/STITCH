use crate::pspec::PSpec;

pub struct Sampler {
    pub pspec: PSpec,
}

impl Sampler {
    pub fn new(pspec: PSpec) -> Self {
        Self { pspec }
    }
}

