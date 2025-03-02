use std::collections::{HashMap, HashSet};

use gf_core::schema::VariableMapping;
use thiserror::Error;
use serde::{Deserialize, Serialize};
use regex::Regex;

use crate::{check, inner_spec::{InnerEndpointSpec, InnerSpec, InnerTypeSpec}};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Config {
    pub headers: Vec<HeaderSpec>,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            headers: vec![],
        }
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub enum HeaderSpec {
    CHeader(String),
    CppHeader(String),
}

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
    pub fn validate(&self, banned_types: &[String]) -> Result<(), String> {
        let mut errors = vec![];

        for endpoint in &self.endpoints {
            match endpoint.validate(banned_types) {
                Ok(_) => (),
                Err(e) => errors.push(e),
            }
        }

        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors.iter().map(|e| e.to_string()).collect::<Vec<_>>().join("\n"))
        }
    }

    pub fn get_object_types(&self, config: &Config) -> HashSet<String> {
        let mut all_types = HashSet::new();

        for endpoint in &self.endpoints {
            for var in &endpoint.variables {
                all_types.insert(var.typ.clone());
            }
        }

        // Check one-by-one
        let mut object_types = HashSet::new();

        for typ in all_types {
            print!("Checking \"{}\"... ", typ);
            match check::is_primitive_constructable(&typ, &config.headers) {
                Ok(true) => {
                    println!("primitive");
                },
                Ok(false) => {
                    println!("object");
                    object_types.insert(typ);
                },
                Err(_) => {
                    println!("err");
                    object_types.insert(typ);
                },
            }
        }

        object_types
    }

    // Generate the inner spec from the external spec
    pub fn generate(&self, config: &Config) -> InnerSpec {
        let object_types = self.get_object_types(config);
        
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

        // replace all instances of "$name" with "[$name]" in the template
        let mut template = Regex::new(r"(\$[a-zA-Z0-9_]+)").unwrap().replace_all(&self.template, "[$1]").to_string();

        let mut post_template = String::new();

        for v in self.variables.iter() {
            match v.dir {
                VariableDir::InOut => {
                    if let Some(idx) = types.get(&v.typ) {
                        inputs.push(*idx);
                        outputs.push(*idx);
                        template = template.replace(&format!("[{}]", v.name), &format!("$i{}", inputs.len() - 1));
                        post_template.push_str(&format!("$o{} = $i{};\n", outputs.len() - 1, inputs.len() - 1));
                        mappings.push(VariableMapping {
                            name: v.name.clone(),
                            in_idx: Some(inputs.len() - 1),
                            out_idx: Some(outputs.len() - 1),
                        });
                    } else {
                        args.push(v.typ.clone());
                        template = template.replace(&format!("[{}]", v.name), &format!("$a{}", args.len() - 1));
                    }
                },
                VariableDir::New => {
                    if let Some(idx) = types.get(&v.typ) {
                        outputs.push(*idx);
                        template = template.replace(&format!("[{}]", v.name), &format!("$o{}", outputs.len() - 1));
                        mappings.push(VariableMapping {
                            name: v.name.clone(),
                            in_idx: None,
                            out_idx: Some(outputs.len() - 1),
                        });
                    } else {
                        // Ignore new arg types
                        // TODO: warning
                        args.push(v.typ.clone());
                        template = template.replace(&format!("[{}]", v.name), &format!("$a{}", args.len() - 1));
                    }
                },
                VariableDir::Del => {
                    if let Some(idx) = types.get(&v.typ) {
                        inputs.push(*idx);
                        template = template.replace(&format!("[{}]", v.name), &format!("$i{}", inputs.len() - 1));
                        mappings.push(VariableMapping {
                            name: v.name.clone(),
                            in_idx: Some(inputs.len() - 1),
                            out_idx: None,
                        });
                    } else {
                        // Ignore del arg types
                        // TODO: warning
                        args.push(v.typ.clone());
                        template = template.replace(&format!("[{}]", v.name), &format!("$a{}", args.len() - 1));
                    }
                },
            }
        }

        template.push_str("\n");
        template.push_str(&post_template);

        InnerEndpointSpec {
            name: self.name.clone(),
            inputs,
            outputs,
            args,
            template,
            mappings,
        }
    }

    pub fn validate(&self, banned_types: &[String]) -> Result<(), EndpointErrors> {
        let t_vars = self.get_templated();
        let r_vars = self.variables.iter().map(|v| v.name.as_str()).collect::<HashSet<_>>();

        let mut errors = vec![];

        for var in &r_vars {
            if !t_vars.contains(var) {
                errors.push(EndpointError::ExtraVar(var.to_string()));
            }
        }

        for var in &t_vars {
            if !r_vars.contains(var) {
                errors.push(EndpointError::MissingVar(var.to_string()));
            }
        }

        errors.sort();

        // Validate valid types
        for var in &self.variables {
            if banned_types.contains(&var.typ) {
                errors.push(EndpointError::InvalidType(var.typ.clone(), format!("Do not request '{}' as a variable type. Construct this variable inside the endpoint instead.", var.typ)));
            } else if let Err(e) = is_valid_type(&var.typ) {
                errors.push(e);
            }
        }

        if errors.is_empty() {
            Ok(())
        } else {
            Err(EndpointErrors {
                name: self.name.clone(),
                errors,
            })
        }
    }

    // Find instances of $<typ>N in the template.
    fn get_templated(&self) -> HashSet<&str> {
        let re = Regex::new(r"(\$\w+)").unwrap();
        re.captures_iter(&self.template)
            .map(|cap| cap.get(1).unwrap().as_str())
            .collect()
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
    fn test_get_templated() {
        let endpoint = EndpointSpec {
            name: "test".to_string(),
            variables: vec![],
            template: "foo + $foo + $bar + $baz[123]".to_string(),
        };

        assert_eq!(endpoint.get_templated(), vec!["$foo", "$bar", "$baz"].iter().cloned().collect());
    }

    #[test]
    fn test_is_valid_type() {
        // Basic types
        assert!(is_valid_type("int").unwrap());
        assert!(is_valid_type("char").unwrap());
        assert!(is_valid_type("void").unwrap());
        assert!(is_valid_type("size_t").unwrap());
        
        // Types with underscore
        assert!(is_valid_type("my_type").unwrap());
        assert!(is_valid_type("_hidden").unwrap());
        
        // Struct types
        assert!(is_valid_type("struct foo").unwrap());
        assert!(is_valid_type("struct my_struct").unwrap());
        
        // Pointer types
        assert!(is_valid_type("int*").unwrap());
        assert!(is_valid_type("char*").unwrap());
        assert!(is_valid_type("struct foo*").unwrap());
        
        // Array types
        assert!(is_valid_type("int[]").unwrap());
        assert!(is_valid_type("char[]").unwrap());
        assert!(is_valid_type("struct foo[]").unwrap());

        // Invalid types
        assert!(matches!(
            is_valid_type("123invalid"),
            Err(EndpointError::InvalidType(_, _))
        ));
        assert!(matches!(
            is_valid_type("invalid!"),
            Err(EndpointError::InvalidType(_, _))
        ));
        assert!(matches!(
            is_valid_type("struct 123foo"),
            Err(EndpointError::InvalidType(_, _))
        ));
        assert!(matches!(
            is_valid_type("*pointer"),
            Err(EndpointError::InvalidType(_, _))
        ));
    }
}
