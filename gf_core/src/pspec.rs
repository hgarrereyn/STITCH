
use serde::{Serialize, Deserialize};

use crate::grammar::{Assignment, Constraint};

use colored::Colorize;


/// Internal (processed) fuzzer specification.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PSpec {
    pub objects: Vec<PObject>,
    pub endpoints: Vec<PEndpoint>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PObject {
    pub name: String,
    pub idx: usize,
    pub attrs: Vec<PAttr>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PAttr {
    pub name: String,
    pub typ: PAttrType,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum PAttrType {
    Int,
    Enum { variants: Vec<String> },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PEndpoint {
    pub name: String,
    pub idx: usize,
    pub inputs: Vec<usize>,
    pub outputs: Vec<usize>,
    pub args: Vec<String>,
    pub template: String,

    // Metadata
    pub input_vars: Vec<String>, // Original variable names
    pub output_vars: Vec<String>, // Original variable names
    pub mappings: Vec<PVariableMapping>,

    pub preconditions: Vec<Constraint>,
    pub effects: Vec<Assignment>,
    pub role: PEndpointRole,
    pub outlives: Vec<POutlivesConstraint>,

    // Filled in at runtime
    pub context_size: Option<usize>,
}

impl Default for PEndpoint {
    fn default() -> Self {
        Self {
            name: "".to_string(),
            idx: 0,
            inputs: vec![],
            outputs: vec![],
            args: vec![],
            template: "".to_string(),
            input_vars: vec![],
            output_vars: vec![],
            mappings: vec![],
            preconditions: vec![],
            effects: vec![],
            context_size: None,
            role: PEndpointRole::None,
            outlives: vec![],
        }
    }
}

impl PartialEq for PEndpoint {
    fn eq(&self, other: &Self) -> bool {
        self.name == other.name &&
        self.inputs == other.inputs && self.outputs == other.outputs && self.args == other.args && self.template == other.template &&
        (self.mappings.len() == other.mappings.len() && self.mappings.iter().zip(other.mappings.iter()).all(|(a, b)| a == b))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PVariableMapping {
    pub name: String,
    pub in_idx: usize,
    pub out_idx: usize,
}

impl PSpec {
    pub fn new() -> Self {
        Self {
            objects: vec![],
            endpoints: vec![],
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum PEndpointRole {
    None,
    LibraryInitializer,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct POutlivesConstraint {
    pub a: String,
    pub b: String,
}


// ────────────────────────────────────────────────────────────────────────────────
//  Pretty printing
// ────────────────────────────────────────────────────────────────────────────────

impl PSpec {
    pub fn pprint(&self) -> String {
        let mut s = String::new();
        s.push_str(&format!("[{}]\n", "Objects".yellow()).to_string());
        for (idx, object) in self.objects.iter().enumerate() {
            s.push_str(&format!("[{}]: {}\n", format!("{}", idx).green(), object.name));
            for attr in &object.attrs {
                match &attr.typ {
                    PAttrType::Int => s.push_str(&format!("  {}: {}\n", attr.name, "int".yellow())),
                    PAttrType::Enum { variants } => {
                        let mut m = String::new();
                        for (i, v) in variants.iter().enumerate() {
                            m.push_str(&format!("{}", v.cyan()));
                            if i < variants.len() - 1 {
                                m.push_str(&format!(", "));
                            }
                        }
                        s.push_str(&format!("  {}: {} ({})\n", attr.name, "enum".yellow(), m));
                    }
                }
            }
        }
        s.push_str(&format!("[{}]\n", "Endpoints".yellow()).to_string());
        for (idx, endpoint) in self.endpoints.iter().enumerate() {
            s.push_str(&format!("[{}]: {}\n", format!("{}", idx).green(), endpoint.name));
            
            // inputs
            s.push_str(&format!("  Inputs: {}\n", endpoint.inputs.iter().map(|i| format!("{}", i).yellow().to_string()).collect::<Vec<String>>().join(", ")));
            // outputs
            s.push_str(&format!("  Outputs: {}\n", endpoint.outputs.iter().map(|i| format!("{}", i).yellow().to_string()).collect::<Vec<String>>().join(", ")));
            // args
            s.push_str(&format!("  Args: {}\n", endpoint.args.iter().map(|arg| format!("{}", arg).yellow().to_string()).collect::<Vec<String>>().join(", ")));
            
            // preconditions
            s.push_str("  Preconditions:\n");
            for precondition in &endpoint.preconditions {
                s.push_str(&format!("    {}\n", precondition.pprint()));
            }
            // effects
            s.push_str("  Effects:\n");
            for effect in &endpoint.effects {
                s.push_str(&format!("    {}\n", effect.pprint()));
            }

            // role
            s.push_str(&format!("  Role: {:?}\n", endpoint.role));
            // outlives
            s.push_str("  Outlives:\n");
            for outlives in &endpoint.outlives {
                s.push_str(&format!("    {} > {}\n", outlives.a, outlives.b));
            }

            s.push_str("--------------------------------\n");
            s.push_str(&format!("{}\n", endpoint.template));
            s.push_str("--------------------------------\n");
        }
        s
    }
}
