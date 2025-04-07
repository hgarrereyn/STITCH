use std::collections::{HashMap, HashSet};

use gf_core::schema::VariableMapping;
use thiserror::Error;
use serde::{Deserialize, Serialize};
use regex::Regex;

use crate::inner_spec::{InnerEndpointSpec, InnerSpec, InnerTypeSpec};

// External spec format
#[derive(Debug, Serialize, Deserialize)]
pub struct Spec {
    pub endpoints: Vec<EndpointSpec>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub enum VariableDir {
    #[serde(rename = "*")]
    InOut,
    #[serde(rename = "new")]
    New,
    #[serde(rename = "del")]
    Del,
}

impl Default for VariableDir {
    fn default() -> Self {
        VariableDir::InOut
    }
}

#[derive(Debug, Serialize, Deserialize)]
pub struct VariableSpec {
    pub name: String,
    pub typ: String,
    #[serde(default)]
    pub dir: VariableDir,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct EndpointSpec {
    pub name: String,
    pub variables: Vec<VariableSpec>,
    pub template: String,
}

#[derive(Error, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum EndpointError {
    #[error("Template reference {0} not defined in vars")]
    MissingVar(String),

    #[error("Extra variable definition {0} not used in template")]
    ExtraVar(String),

    #[error("Invalid type {0}, with reason {1}")]
    InvalidType(String, String),
}

#[derive(Debug, PartialEq, Eq)]
pub struct EndpointErrors {
    pub name: String,
    pub errors: Vec<EndpointError>,
}

#[derive(Debug)]
pub struct SpecErrors {
    pub errors: Vec<EndpointErrors>,
}

impl std::fmt::Display for EndpointErrors {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        write!(f, "Endpoint \"{}\" has errors:", self.name)?;
        for error in &self.errors {
            write!(f, "\n  {}", error)?;
        }
        Ok(())
    }
}

impl std::fmt::Display for SpecErrors {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        for error in &self.errors {
            write!(f, "{}\n", error)?;
        }
        Ok(())
    }
}

impl Spec {
    pub fn get_object_types(&self) -> HashSet<String> {
        let mut all_types = HashSet::new();

        for endpoint in &self.endpoints {
            for var in &endpoint.variables {
                all_types.insert(var.typ.clone());
            }
        }

        all_types
    }

    // Generate the inner spec from the external spec
    pub fn generate(&self) -> InnerSpec {
        let object_types = self.get_object_types();
        
        // Un-constructable types are treated as args -- these will be validated during compilation
        let mut types = object_types.iter().map(|t| InnerTypeSpec { name: t.to_string() }).collect::<Vec<_>>();
        types.sort();

        let types_to_idx = types.iter().enumerate().map(|(i, t)| (t.name.clone(), i)).collect::<HashMap<_, _>>();

        let endpoints = self.endpoints.iter().map(|e| e.generate(&types_to_idx)).collect();

        InnerSpec {
            types,
            endpoints
        }
    }
}

impl EndpointSpec {
    pub fn generate(&self, types: &HashMap<String, usize>) -> InnerEndpointSpec {
        let mut inputs = vec![];
        let mut outputs = vec![];
        let mut args = vec![];

        let mut mappings = vec![];

        // Replace all instances of a variable name surrounded by non-word characters with "@@name@@"
        let mut template = self.template.clone();
        for v in self.variables.iter() {
            let re = Regex::new(&format!(r"(\b{}\b)", v.name)).unwrap();
            template = re.replace_all(&template, &format!("@@{}@@", v.name)).to_string();
        }

        let mut pre_template = String::new();
        let mut post_template = String::new();

        for v in self.variables.iter() {
            match v.dir {
                VariableDir::InOut => {
                    if let Some(idx) = types.get(&v.typ) {
                        inputs.push(*idx);
                        outputs.push(*idx);
                        template = template.replace(&format!("@@{}@@", v.name), &format!("$i{}", inputs.len() - 1));
                        post_template.push_str(&format!("$o{} = $i{};\n", outputs.len() - 1, inputs.len() - 1));
                        mappings.push(VariableMapping {
                            name: v.name.clone(),
                            in_idx: Some(inputs.len() - 1),
                            out_idx: Some(outputs.len() - 1),
                        });
                    } else {
                        args.push(v.typ.clone());
                        template = template.replace(&format!("@@{}@@", v.name), &format!("$a{}", args.len() - 1));
                    }
                },
                VariableDir::New => {
                    if let Some(idx) = types.get(&v.typ) {
                        outputs.push(*idx);
                        template = template.replace(&format!("@@{}@@", v.name), &format!("$o{}", outputs.len() - 1));
                        mappings.push(VariableMapping {
                            name: v.name.clone(),
                            in_idx: None,
                            out_idx: Some(outputs.len() - 1),
                        });
                    } else {
                        // Ignore new arg types
                        // TODO: warning
                        args.push(v.typ.clone());
                        template = template.replace(&format!("@@{}@@", v.name), &format!("$a{}", args.len() - 1));
                    }
                },
                VariableDir::Del => {
                    if let Some(idx) = types.get(&v.typ) {
                        inputs.push(*idx);
                        template = template.replace(&format!("@@{}@@", v.name), &format!("$i{}", inputs.len() - 1));
                        mappings.push(VariableMapping {
                            name: v.name.clone(),
                            in_idx: Some(inputs.len() - 1),
                            out_idx: None,
                        });
                    } else {
                        // Ignore del arg types
                        // TODO: warning
                        args.push(v.typ.clone());
                        template = template.replace(&format!("@@{}@@", v.name), &format!("$a{}", args.len() - 1));
                    }
                },
            }
        }

        {
            // Find all instances of FUZZ_PARAM(<T>), and replace them with $a{i}
            let fuzz_param_re = Regex::new(r"FUZZ_PARAM\(([^)]+)\)").unwrap();
            let mut template_with_fuzz = template.clone();

            // Find all instances of FUZZ_PARAM
            let matches: Vec<_> = fuzz_param_re.find_iter(&template).collect();
            for m in matches {
                let cap = fuzz_param_re.captures(&template[m.start()..m.end()]).unwrap();
                let param_type = cap.get(1).unwrap().as_str().trim().to_string();
                
                // Add to args
                args.push(param_type);
                
                // Replace with the appropriate $a{i} reference
                let replacement = format!("$a{}", args.len() - 1);
                template_with_fuzz = template_with_fuzz.replacen(&template[m.start()..m.end()], &replacement, 1);
            }

            // Use the updated template with FUZZ_PARAM replaced
            template = template_with_fuzz;
        }
        {
            // Find all instances of FUZZ_PARAM_STR(<N>) and replace them with (char *)$aN
            let fuzz_param_str_re = Regex::new(r"FUZZ_PARAM_STR\((\d+)\)").unwrap();
            let mut template_with_fuzz = template.clone();

            let matches: Vec<_> = fuzz_param_str_re.find_iter(&template).collect();
            for m in matches {
                let cap = fuzz_param_str_re.captures(&template[m.start()..m.end()]).unwrap();
                let len = cap.get(1).unwrap().as_str().trim().parse::<usize>().unwrap();

                // Add to args
                args.push(format!("char[{}]", len + 1));

                // Null terminate the string
                pre_template.push_str(&format!("$a{}[{}] = 0;\n", args.len() - 1, len));

                // Replace with the appropriate $a{i} reference
                let replacement = format!("((const char *)$a{})", args.len() - 1);
                template_with_fuzz = template_with_fuzz.replacen(&template[m.start()..m.end()], &replacement, 1);
            }

            // Use the updated template with FUZZ_PARAM_STR replaced
            template = template_with_fuzz;
        }
        {
            // Find all instances of FUZZ_PARAM_FILE(<N>) and replace them with gf_fuzz_param::write_to_temp_file($aN, N)
            let fuzz_param_file_re = Regex::new(r"FUZZ_PARAM_FILE\((\d+)\)").unwrap();
            let mut template_with_fuzz = template.clone();

            let matches: Vec<_> = fuzz_param_file_re.find_iter(&template).collect();
            for m in matches {
                let cap = fuzz_param_file_re.captures(&template[m.start()..m.end()]).unwrap();
                let len = cap.get(1).unwrap().as_str().trim().parse::<usize>().unwrap();

                // Add to args
                args.push(format!("char[{}]", len));

                // Replace with the appropriate $a{i} reference
                let replacement = format!("gf_fuzz_param::write_to_temp_file($a{}, {})", args.len() - 1, len);
                template_with_fuzz = template_with_fuzz.replacen(&template[m.start()..m.end()], &replacement, 1);
            }

            // Use the updated template with FUZZ_PARAM_FILE replaced
            template = template_with_fuzz;
        }

        let mut full_template = String::new();
        full_template.push_str(&pre_template);
        full_template.push_str("\n");
        full_template.push_str(&template);
        full_template.push_str("\n");
        full_template.push_str(&post_template);

        InnerEndpointSpec {
            name: self.name.clone(),
            inputs,
            outputs,
            args,
            template: full_template,
            mappings,
        }
    }
}

const PRIMITIVE_TYPES: &[&str] = &[
    "bool",
    "char",
    "signed char",
    "unsigned char",
    "short",
    "short int", 
    "signed short",
    "signed short int",
    "unsigned short",
    "unsigned short int",
    "int",
    "signed",
    "signed int",
    "unsigned",
    "unsigned int",
    "long",
    "long int",
    "signed long",
    "signed long int", 
    "unsigned long",
    "unsigned long int",
    "long long",
    "long long int",
    "signed long long",
    "signed long long int",
    "unsigned long long",
    "unsigned long long int",
    "float",
    "double",
    "long double",
    "wchar_t",
    "char8_t",
    "char16_t", 
    "char32_t",
    "size_t",
    "ssize_t",
    "ptrdiff_t",
    "intptr_t",
    "uintptr_t",
    "int8_t",
    "uint8_t",
    "int16_t",
    "uint16_t",
    "int32_t",
    "uint32_t",
    "int64_t",
    "uint64_t",
];

pub fn is_valid_type(typ: &str) -> Result<bool, EndpointError> {
    // Check for void* which is invalid
    if typ.trim() == "void*" || typ.trim() == "void *" {
        return Err(EndpointError::InvalidType(
            typ.to_string(),
            "void* is not allowed as a type".to_string()
        ));
    }

    // "const char *" is invalid
    if typ.trim() == "const char *" || typ.trim() == "char *" {
        return Err(EndpointError::InvalidType(
            typ.to_string(),
            "char * is not allowed, use a fixed-size char array instead".to_string()
        ));
    }

    // Check for const type which is invalid
    if typ.trim().starts_with("const") {
        return Err(EndpointError::InvalidType(
            typ.to_string(),
            "const types are not allowed".to_string()
        ));
    }

    // Pointers to primitive types are invalid
    let pointer_re = Regex::new(r"^([a-zA-Z_][a-zA-Z0-9_ ]*?)\*$").unwrap();
    if let Some(captures) = pointer_re.captures(typ) {
        let base_type = captures.get(1).unwrap().as_str().trim();
        if PRIMITIVE_TYPES.contains(&base_type) {
            return Err(EndpointError::InvalidType(
                typ.to_string(),
                "Pointers to primitive types are not allowed, use the primitive type directly".to_string()
            ));
        }
    }

    // Check for pointer type (foo*)
    let pointer_re = Regex::new(r"^([a-zA-Z_][a-zA-Z0-9_ ]*?)\*$").unwrap();
    if pointer_re.is_match(typ) {
        return Ok(true);
    }

    // Check for array type (foo[N])
    let array_re = Regex::new(r"^([a-zA-Z_][a-zA-Z0-9_ ]*?)\[\d+\]$").unwrap();
    if array_re.is_match(typ) {
        return Ok(true);
    }

    // Check for basic type (foo)
    let basic_re = Regex::new(r"^[a-zA-Z_][a-zA-Z0-9_ ]*$").unwrap();
    if basic_re.is_match(typ) {
        return Ok(true);
    }

    // If none of the patterns match, determine specific error message
    if typ.contains('*') {
        return Err(EndpointError::InvalidType(
            typ.to_string(),
            "Invalid pointer type format - must be a valid identifier followed by *".to_string()
        ));
    } else if typ.contains('[') || typ.contains(']') {
        return Err(EndpointError::InvalidType(
            typ.to_string(),
            "Invalid array type format - must be a valid identifier followed by [N] where N is a number".to_string()
        ));
    }
    
    Err(EndpointError::InvalidType(
        typ.to_string(),
        "Invalid type name - must start with letter or underscore and contain only letters, numbers, underscores and spaces".to_string()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_generate() {
        let spec = Spec {
            endpoints: vec![EndpointSpec {
                name: "test".to_string(),
                variables: vec![VariableSpec { name: "_foo".to_string(), typ: "int".to_string(), dir: VariableDir::InOut }],
                template: "_foo += 1;".to_string(),
            }],
        };

        let inner_spec = spec.generate();
        let tmpl = inner_spec.endpoints[0].template.clone();
    
        assert!(tmpl.contains("$a0 += 1;"));
    }

    #[test]
    fn test_generate_obj() {
        let spec = Spec {
            endpoints: vec![EndpointSpec {
                name: "test".to_string(),
                variables: vec![VariableSpec { name: "_foo".to_string(), typ: "void *".to_string(), dir: VariableDir::InOut }],
                template: "void *foo = _foo;".to_string(),
            }],
        };

        let inner_spec = spec.generate();
        let tmpl = inner_spec.endpoints[0].template.clone();

        println!("{}", tmpl);

        assert!(tmpl.contains("void *foo = $i0;"));
        assert!(tmpl.contains("$o0 = $i0;"));
    }

    #[test]
    fn test_generate_obj_new() {
        let spec = Spec {
            endpoints: vec![EndpointSpec {
                name: "test".to_string(),
                variables: vec![VariableSpec { name: "_foo".to_string(), typ: "void *".to_string(), dir: VariableDir::New }],
                template: "_foo = new void *();".to_string(),
            }],
        };

        let inner_spec = spec.generate();
        let tmpl = inner_spec.endpoints[0].template.clone();

        assert!(tmpl.contains("$o0 = new void *();"));
    }

    #[test]
    fn test_generate_obj_del() {
        let spec = Spec {
            endpoints: vec![EndpointSpec {
                name: "test".to_string(),
                variables: vec![VariableSpec { name: "_foo".to_string(), typ: "void *".to_string(), dir: VariableDir::Del }],
                template: "_foo = nullptr;".to_string(),
            }],
        };
    
        let inner_spec = spec.generate();
        let tmpl = inner_spec.endpoints[0].template.clone();

        assert!(tmpl.contains("$i0 = nullptr;"));
        assert!(!tmpl.contains("$o0 = $i0;"));
    }
}
