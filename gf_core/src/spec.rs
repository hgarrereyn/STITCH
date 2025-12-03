use std::{collections::HashMap, fmt};

use regex::Regex;
use serde::{Serialize, Deserialize};

use crate::{pspec::{PAttr, PAttrType, PEndpoint, PEndpointRole, PObject, POutlivesConstraint, PSpec, PVariableMapping}};


// ------------------------------------------------------------------------------------------------
// External fuzzer specification
// ------------------------------------------------------------------------------------------------

/// External fuzzer specification.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Spec {
    pub objects: Vec<Object>,
    pub endpoints: Vec<Endpoint>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Object {
    pub name: String,

    #[serde(default)]
    pub attrs: Vec<Attr>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum AttrType {
    #[serde(rename = "int")]
    Int,
    #[serde(rename = "enum")]
    Enum { variants: Vec<String> },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Attr {
    pub name: String,
    #[serde(flatten)]
    pub attr_type: AttrType,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Endpoint {
    pub name: String,

    #[serde(default)]
    pub variables: Vec<Variable>,

    pub template: String,

    #[serde(default)]
    pub hints: Vec<String>,

    #[serde(default)]
    pub preconditions: Vec<String>,

    #[serde(default)]
    pub effects: Vec<String>,

    #[serde(default)]
    pub role: EndpointRole,

    #[serde(default)]
    pub outlives: Vec<OutlivesConstraint>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Variable {
    pub name: String,
    #[serde(rename = "type")]
    pub typ: String,
    #[serde(default)]
    pub dir: VariableDir,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EndpointRole {
    #[serde(rename = "none")]
    None,
    #[serde(rename = "library_initializer")]
    LibraryInitializer,
}

impl Default for EndpointRole {
    fn default() -> Self {
        EndpointRole::None
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OutlivesConstraint {
    pub a: String,
    pub b: String,
}

// ------------------------------------------------------------------------------------------------
// Validation errors
// ------------------------------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum ValidationError {
    Endpoint(usize, String, EndpointValidationError),
}

impl fmt::Display for ValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ValidationError::Endpoint(idx, name, error) => write!(f, "Endpoint \"{}\" at index {} is invalid:\n{}", name, idx, error),
        }
    }
}


#[derive(Debug, Clone)]
pub enum EndpointValidationError {
    // DuplicateEffectTarget(Leaf, Vec<Assignment>),
    // ParsingError(String, GrammarError),
    CodeBlockError(String),
}


impl fmt::Display for EndpointValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            // EndpointValidationError::DuplicateEffectTarget(_target, effects) => write!(f, "More than one effect for the same target: ({})", effects.iter().map(|e| e.to_string()).collect::<Vec<_>>().join(", ")),
            // EndpointValidationError::ParsingError(expr, error) => write!(f, "Error while parsing \"{}\":\n{}", expr, error),
            EndpointValidationError::CodeBlockError(error) => write!(f, "Error while parsing code block:\n{}", error),
        }
    }
}
impl Spec {
    pub fn build_pspec(&self) -> Result<PSpec, Vec<ValidationError>> {
        let mut pspec = PSpec::new();
        let mut spec_errors = vec![];

        let mut object_idx = 0;
        for object in self.objects.iter() {
            pspec.objects.push(PObject {
                name: object.name.clone(),
                idx: object_idx,
                attrs: object.attrs.iter().map(|attr| PAttr {
                    name: attr.name.clone(),
                    typ: match &attr.attr_type {
                        AttrType::Int => PAttrType::Int,
                        AttrType::Enum { variants } => PAttrType::Enum { variants: variants.clone() },
                    },
                }).collect(),
            });
            object_idx += 1;
        }

        let type_to_idx = self.objects.iter().enumerate().map(|(i, o)| (o.name.clone(), i)).collect::<HashMap<_, _>>();

        let mut endpoint_idx = 0;
        for endpoint in self.endpoints.iter() {
            let res = endpoint.build_pspec(&type_to_idx, endpoint_idx, &pspec.objects);
            match res {
                Ok(ep) => pspec.endpoints.push(ep),
                Err(errors) => spec_errors.extend(
                    errors.into_iter().map(|e| ValidationError::Endpoint(endpoint_idx, endpoint.name.clone(), e))
                ),
            }
            endpoint_idx += 1;
        }

        if !spec_errors.is_empty() {
            return Err(spec_errors);
        }

        Ok(pspec)
    }
}

impl Endpoint {
    pub fn build_pspec(&self, type_to_idx: &HashMap<String, usize>, endpoint_idx: usize, objects: &[PObject]) -> Result<PEndpoint, Vec<EndpointValidationError>> {
        // let mut endpoint_errors = vec![];
        
        let mut inputs = vec![];
        let mut outputs = vec![];
        let mut args = vec![];

        let mut input_vars = vec![];
        let mut output_vars = vec![];
        let mut mappings = vec![];

        let mut pre_template = String::new();
        let mut post_template = String::new();

        let mut hint_args = vec![];
        let mut hint_args_sizes = vec![];
        let mut flat_args = vec![];

        let mut template = self.template.clone();

        // // Replace all instances of a variable name surrounded by non-word characters with "@@name@@"
        // let mut template = self.template.clone();
        // for v in self.variables.iter() {
        //     let re = Regex::new(&format!(r"(\b{}\b)", v.name)).unwrap();
        //     template = re.replace_all(&template, &format!("@@{}@@", v.name)).to_string();
        // }

        // for v in self.variables.iter() {
        //     match v.dir {
        //         VariableDir::InOut => {
        //             if let Some(idx) = type_to_idx.get(&v.typ) {
        //                 inputs.push(*idx);
        //                 outputs.push(*idx);
        //                 template = template.replace(&format!("@@{}@@", v.name), &format!("$i{}", inputs.len() - 1));
        //                 post_template.push_str(&format!("$o{} = $i{};\n", outputs.len() - 1, inputs.len() - 1));
        //                 input_vars.push(v.name.clone());
        //                 output_vars.push(v.name.clone());
        //                 mappings.push(PVariableMapping {
        //                     name: v.name.clone(),
        //                     in_idx: inputs.len() - 1,
        //                     out_idx: outputs.len() - 1,
        //                 });
        //             } else {
        //                 args.push(v.typ.clone());
        //                 template = template.replace(&format!("@@{}@@", v.name), &format!("$a{}", args.len() - 1));
        //             }
        //         },
        //         VariableDir::New => {
        //             if let Some(idx) = type_to_idx.get(&v.typ) {
        //                 outputs.push(*idx);
        //                 template = template.replace(&format!("@@{}@@", v.name), &format!("$o{}", outputs.len() - 1));
        //                 output_vars.push(v.name.clone());
        //                 // mappings.push(PVariableMapping {
        //                 //     name: v.name.clone(),
        //                 //     in_idx: None,
        //                 //     out_idx: Some(outputs.len() - 1),
        //                 // });
        //             } else {
        //                 // Ignore new arg types
        //                 // TODO: warning
        //                 args.push(v.typ.clone());
        //                 template = template.replace(&format!("@@{}@@", v.name), &format!("$a{}", args.len() - 1));
        //             }
        //         },
        //         VariableDir::Del => {
        //             if let Some(idx) = type_to_idx.get(&v.typ) {
        //                 inputs.push(*idx);
        //                 template = template.replace(&format!("@@{}@@", v.name), &format!("$i{}", inputs.len() - 1));
        //                 input_vars.push(v.name.clone());
        //                 // mappings.push(PVariableMapping {
        //                 //     name: v.name.clone(),
        //                 //     in_idx: Some(inputs.len() - 1),
        //                 //     out_idx: None,
        //                 // });
        //             } else {
        //                 // Ignore del arg types
        //                 // TODO: warning
        //                 args.push(v.typ.clone());
        //                 template = template.replace(&format!("@@{}@@", v.name), &format!("$a{}", args.len() - 1));
        //             }
        //         },
        //     }
        // }

        for v in self.variables.iter() {
            match v.dir {
                VariableDir::InOut => {
                    let idx = type_to_idx.get(&v.typ).unwrap();
                    inputs.push(*idx);
                    outputs.push(*idx);
                    pre_template.push_str(&format!("{} {} = $i{};\n", v.typ, v.name, inputs.len() - 1));
                    post_template.push_str(&format!("$o{} = {};\n", outputs.len() - 1, v.name));
                    input_vars.push(v.name.clone());
                    output_vars.push(v.name.clone());
                    mappings.push(PVariableMapping {
                        name: v.name.clone(),
                        in_idx: inputs.len() - 1,
                        out_idx: outputs.len() - 1,
                    });
                }
                VariableDir::New => {
                    let idx = type_to_idx.get(&v.typ).unwrap();
                    outputs.push(*idx);
                    post_template.push_str(&format!("$o{} = {};\n", outputs.len() - 1, v.name));
                    output_vars.push(v.name.clone());
                }
                VariableDir::Del => {
                    let idx = type_to_idx.get(&v.typ).unwrap();
                    inputs.push(*idx);
                    pre_template.push_str(&format!("{} {} = $i{};\n", v.typ, v.name, inputs.len() - 1));
                    input_vars.push(v.name.clone());
                }
            }
        }

        {
            let mut template_with_fuzz = template.clone();

            // Find all instances of FUZZ_PARAM(<T>) and replace them with (&$a{i})
            let matches = find_macro_invocations(&template, "FUZZ_PARAM");
            for (start, end, arg) in matches {
                args.push(arg);
                template_with_fuzz = template_with_fuzz.replacen(&template[start..end], &format!("(&$a{})", args.len() - 1), 1);

                flat_args.push(args.len() - 1);
            }

            template = template_with_fuzz;
        }
        {
            let mut template_with_fuzz = template.clone();

            // Find all instances of FUZZ_PARAM_STR() and replace them with (char *)$aN
            let matches = find_macro_invocations(&template, "FUZZ_PARAM_STR");
            for (start, end, arg) in matches {
                args.push("uint32_t".to_string()); // size
                args.push("unsigned char[4096]".to_string()); // data

                let arg_buf_size = args.len() - 2;
                let arg_buf_data = args.len() - 1;

                pre_template.push_str(&format!("std::string $__str_{} = std::string((const char *)$a{}, (size_t)$a{});", arg_buf_size, arg_buf_data, arg_buf_size));
                template_with_fuzz = template_with_fuzz.replacen(&template[start..end], &format!("($__str_{})", arg_buf_size), 1);

                hint_args.push(arg_buf_size);
                hint_args_sizes.push(4096);
            }

            template = template_with_fuzz;
        }
        {
            let mut template_with_fuzz = template.clone();

            // Find all instances of FUZZ_PARAM_FILE() and replace them with gf_fuzz_param::write_to_temp_file($aN, 1024)
            let matches = find_macro_invocations(&template, "FUZZ_PARAM_FILENAME");
            for (start, end, arg) in matches {
                args.push("uint32_t".to_string()); // size
                args.push("unsigned char[4096]".to_string()); // data
                template_with_fuzz = template_with_fuzz.replacen(&template[start..end], &format!("gf_fuzz_param::write_to_temp_file($a{}, $a{})", args.len() - 1, args.len() - 2), 1);

                hint_args.push(args.len() - 2); // pointing to the size
                hint_args_sizes.push(4096);
            }

            template = template_with_fuzz;
        }

        let mut full_template = String::new();
        if !pre_template.is_empty() {
            full_template.push_str(&pre_template);
        }
        full_template.push_str(&template);
        if !post_template.is_empty() {
            full_template.push_str("\n");
            full_template.push_str(&post_template);
        }

        /*
        // Precondition references input objects
        let precondition_ctx = self.build_context(&inputs, &input_vars, objects);
        let precondition_parser = ParserWithCtx::new(&precondition_ctx);
        
        let mut preconditions = vec![];
        for p in self.preconditions.iter() {
            match precondition_parser.parse_constraint(p) {
                Ok(c) => preconditions.push(c),
                Err(e) => endpoint_errors.push(EndpointValidationError::ParsingError(p.clone(), e)),
            }
        }

        // Effect references output objects
        let effect_ctx = self.build_context(&outputs, &output_vars, objects);
        let effect_parser = ParserWithCtx::new(&effect_ctx);
        
        let mut effects = vec![];
        for p in self.effects.iter() {
            match effect_parser.parse_assignment(p) {
                Ok(c) => effects.push(c),
                Err(e) => endpoint_errors.push(EndpointValidationError::ParsingError(p.clone(), e)),
            }
        }

        // Ensure there are no duplicate effect targets
        let mut effect_targets = HashMap::new();
        for effect in effects.iter() {
            effect_targets.entry(effect.target.clone()).or_insert_with(Vec::new).push(effect.clone());
        }

        for (target, effects) in effect_targets.iter() {
            if effects.len() > 1 {
                endpoint_errors.push(EndpointValidationError::DuplicateEffectTarget(target.clone(), effects.clone()));
            }
        }
        */

        Ok(PEndpoint {
            name: self.name.clone(),
            idx: endpoint_idx,
            inputs,
            outputs,
            args,
            template: full_template,

            hints: self.hints.clone(),
            hint_args,
            hint_args_sizes,
            flat_args,

            input_vars,
            output_vars,
            mappings,

            // Autogenerated
            context_size: None,
            arg_offsets: vec![],
            arg_sizes: vec![],
        })
    }

    // fn build_context(&self, indexes: &[usize], names: &[String], objects: &[PObject]) -> Context {
    //     let mut vars = HashMap::new();

    //     for (idx, name) in indexes.iter().zip(names.iter()) {
    //         let object = &objects[*idx];
    //         let object_name = &object.name;
        
    //         let mut fields = HashMap::new();
    //         for attr in object.attrs.iter() {
    //             fields.insert(attr.name.clone(), match &attr.typ {
    //                 PAttrType::Int => Ty::Int,
    //                 PAttrType::Enum { .. } => Ty::Enum(format!("{}::{}", object_name, attr.name)),
    //             });
    //         }

    //         vars.insert(name.clone(), VarDef {
    //             name: name.clone(),
    //             fields,
    //         });
    //     }

    //     let mut enums = HashMap::new();

    //     for idx in indexes.iter() {
    //         let object = &objects[*idx];
    //         let object_name = &object.name;

    //         for attr in object.attrs.iter() {
    //             if let PAttrType::Enum { variants } = &attr.typ {
    //                 enums.insert(format!("{}::{}", object_name, attr.name), EnumDef {
    //                     name: format!("{}::{}", object_name, attr.name),
    //                     variants: variants.iter().map(|v| v.clone()).collect(),
    //                 });
    //             }
    //         }
    //     }
        
    //     Context {
    //         vars,
    //         globals: HashMap::new(),
    //         enums,
    //     }
    // }
}

pub fn find_macro_invocations<'a>(input: &'a str, keyword: &str) -> Vec<(usize, usize, String)> {
    let mut result = Vec::new();
    let search = format!("{keyword}(");
    let input_bytes = input.as_bytes();
    let mut pos = 0;

    while let Some(start) = input[pos..].find(&search) {
        let start = pos + start;
        let mut depth = 1;
        let mut i = start + search.len();
        let mut last = i;
        while i < input.len() && depth > 0 {
            match input_bytes[i] as char {
                '(' => depth += 1,
                ')' => {
                    depth -= 1;
                    if depth == 0 {
                        // Found matching close
                        result.push((
                            start,
                            i + 1,
                            input[(start + search.len())..i].to_string(),
                        ));
                        last = i + 1;
                        break;
                    }
                }
                _ => {}
            }
            i += 1;
        }
        pos = last; // Continue searching after this invocation
    }
    result
}

#[cfg(test)]
mod tests {
    // use crate::grammar::{BoolAtom, BoolExpr, CmpOp, Constraint, EnumExpr, Expr, IntAtom, IntExpr, Leaf};

    use super::*;
    
    #[test]
    fn test_convert_fuzz_param() {
        let spec_json = r#"
        {
            "objects": [],
            "endpoints": [
                {
                    "name": "test_func",
                    "variables": [],
                    "template": "int z = *FUZZ_PARAM(int);"
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        
        assert_eq!(pspec.endpoints[0], PEndpoint {
            name: "test_func".to_string(),
            idx: 0,
            inputs: vec![],
            outputs: vec![],
            args: vec!["int".to_string()],
            template: "int z = *(&$a0);".to_string(),
            mappings: vec![],
            ..Default::default()
        });
    }

    #[test]
    fn test_convert_fuzz_param_str() {
        let spec_json = r#"
        {
            "objects": [],
            "endpoints": [
                {
                    "name": "test_func",
                    "variables": [],
                    "template": "int z = FUZZ_PARAM_STR(10);"
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap() ;
        
        assert_eq!(pspec.endpoints[0], PEndpoint {
            name: "test_func".to_string(),
            idx: 0,
            inputs: vec![],
            outputs: vec![],
            args: vec!["char[11]".to_string()],
            template: "$a0[10] = 0;\nint z = ((const char *)$a0);".to_string(),
            mappings: vec![],
            ..Default::default()
        });
    }

    #[test]
    fn test_convert_fuzz_param_str_invalid() {
        let spec_json = r#"
        {
            "objects": [],
            "endpoints": [
                {
                    "name": "test_func",
                    "variables": [],
                    "template": "int z = FUZZ_PARAM_STR(10 + 1);"
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap() ;
        
        assert_eq!(pspec.endpoints[0], PEndpoint {
            name: "test_func".to_string(),
            idx: 0,
            inputs: vec![],
            outputs: vec![],
            args: vec!["char[11]".to_string()],
            template: "$a0[10] = 0;\nint z = ((const char *)$a0);".to_string(),
            mappings: vec![],
            ..Default::default()
        });
    }

    #[test]
    fn test_convert_fuzz_param_file() {
        let spec_json = r#"
        {
            "objects": [],
            "endpoints": [
                {
                    "name": "test_func",
                    "variables": [],
                    "template": "int z = FUZZ_PARAM_FILE();"
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        
        assert_eq!(pspec.endpoints[0], PEndpoint {
            name: "test_func".to_string(),
            idx: 0,
            inputs: vec![],
            outputs: vec![],
            args: vec!["char[1024]".to_string()],
            template: "int z = gf_fuzz_param::write_to_temp_file($a0, 1024);".to_string(),
            mappings: vec![],
            ..Default::default()
        });
    }

    // #[test]
    // fn test_encode_constraints() {
    //     let spec_json = r#"
    //     {
    //         "objects": [
    //             {
    //                 "name": "foo",
    //                 "attrs": [
    //                     {
    //                         "name": "x",
    //                         "type": "int"
    //                     },
    //                     {
    //                         "name": "y",
    //                         "type": "enum",
    //                         "variants": ["A", "B", "C"]
    //                     }
    //                 ]
    //             }
    //         ],
    //         "endpoints": [
    //             {
    //                 "name": "test_func",
    //                 "variables": [
    //                     {
    //                         "name": "z",
    //                         "type": "foo",
    //                         "dir": "*"
    //                     }
    //                 ],
    //                 "template": "test_func(z);",
    //                 "preconditions": [
    //                     "z.x > 0"
    //                 ],
    //                 "effects": [
    //                     "z.x := 5",
    //                     "z.y := B"
    //                 ]
    //             }
    //         ]
    //     }
    //     "#;
    //     let spec: Spec = serde_json::from_str(spec_json).unwrap();
    //     let pspec = spec.build_pspec().unwrap();
        
    //     println!("{}", pspec.pprint());

    //     assert_eq!(pspec.endpoints[0], PEndpoint {
    //         name: "test_func".to_string(),
    //         idx: 0,
    //         inputs: vec![0],
    //         outputs: vec![0],
    //         args: vec![],
    //         template: "foo z = $i0;\ntest_func(z);\n$o0 = z;\n".to_string(),
    //         input_vars: vec!["z".to_string()],
    //         output_vars: vec!["z".to_string()],
    //         mappings: vec![PVariableMapping {
    //             name: "z".to_string(),
    //             in_idx: 0,
    //             out_idx: 0,
    //         }],
    //         preconditions: vec![Constraint {
    //             expr: BoolExpr::Atom(BoolAtom::Cmp(IntExpr::Atom(IntAtom::Leaf(Leaf::Var("z".to_string(), "x".to_string()))), CmpOp::Gt, IntExpr::Atom(IntAtom::IntLit(0))))
    //         }],
    //         effects: vec![Assignment {
    //             target: Leaf::Var("z".to_string(), "x".to_string()),
    //             expr: Expr::Int(IntExpr::Atom(IntAtom::IntLit(5))),
    //         }, Assignment {
    //             target: Leaf::Var("z".to_string(), "y".to_string()),
    //             expr: Expr::Enum(EnumExpr::Variant {
    //                 enum_name: Some("foo::y".to_string()),
    //                 variant: "B".to_string(),
    //             }),
    //         }],
    //         context_size: None,
    //         ..Default::default()
    //     });
    // }

    #[test]
    fn test_encode_complex() {
        let spec_json = r#"
        {
            "objects": [
                {
                    "name": "foo",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "int"
                        },
                        {
                            "name": "y",
                            "type": "enum",
                            "variants": ["A", "B", "C"]
                        }
                    ]
                },
                {
                    "name": "bar",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "enum",
                            "variants": ["FOO", "BAR"]
                        },
                        {
                            "name": "y",
                            "type": "int"
                        }
                    ]
                }
            ],
            "endpoints": [
                {
                    "name": "test_func",
                    "variables": [
                        {
                            "name": "a",
                            "type": "foo",
                            "dir": "*"
                        },
                        {
                            "name": "b",
                            "type": "bar",
                            "dir": "*"
                        },
                        {
                            "name": "c",
                            "type": "bar",
                            "dir": "new"
                        }
                    ],
                    "template": "test_func(a, b, c);",
                    "preconditions": [
                        "a.x > 0",
                        "b.x is FOO"
                    ],
                    "effects": [
                        "a.x := 5",
                        "a.y := C",
                        "b.y := 10",
                        "c.x := FOO",
                        "c.y := 20 + a.x"
                    ]
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        
        println!("{}", pspec.pprint());
    }

    #[test]
    fn test_encode_() {
        let spec_json = r#"
        {
            "objects": [
                {
                    "name": "foo",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "int"
                        },
                        {
                            "name": "y",
                            "type": "enum",
                            "variants": ["A", "B", "C"]
                        }
                    ]
                },
                {
                    "name": "bar",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "enum",
                            "variants": ["FOO", "BAR"]
                        },
                        {
                            "name": "y",
                            "type": "int"
                        }
                    ]
                }
            ],
            "endpoints": [
                {
                    "name": "test_func",
                    "variables": [
                        {
                            "name": "a",
                            "type": "foo",
                            "dir": "*"
                        },
                        {
                            "name": "b",
                            "type": "bar",
                            "dir": "*"
                        },
                        {
                            "name": "c",
                            "type": "bar",
                            "dir": "new"
                        }
                    ],
                    "template": "test_func(a, b, c);",
                    "preconditions": [
                        "a.x > 0",
                        "b.x is FOO"
                    ],
                    "effects": [
                        "a.x := 5",
                        "a.y := C",
                        "b.y := 10",
                        "c.x := FOO",
                        "c.y := 20 + a.x"
                    ]
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        
        println!("{}", pspec.pprint());
    }

    #[test]
    fn test_spec_errors() {
        let spec_json = r#"
        {
            "objects": [
                {
                    "name": "foo",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "int"
                        },
                        {
                            "name": "y",
                            "type": "enum",
                            "variants": ["A", "B", "C"]
                        }
                    ]
                },
                {
                    "name": "bar",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "enum",
                            "variants": ["FOO", "BAR"]
                        },
                        {
                            "name": "y",
                            "type": "int"
                        }
                    ]
                }
            ],
            "endpoints": [
                {
                    "name": "test_func",
                    "variables": [
                        {
                            "name": "a",
                            "type": "foo",
                            "dir": "*"
                        },
                        {
                            "name": "b",
                            "type": "bar",
                            "dir": "*"
                        },
                        {
                            "name": "c",
                            "type": "bar",
                            "dir": "*"
                        }
                    ],
                    "template": "test_func(a, b, c);",
                    "preconditions": [
                        "a.x > 0",
                        "b.x is FOO",
                        "zorph + 234"
                    ],
                    "effects": [
                        "a.x := scree",
                        "b.y := 10",
                        "b.y := 11"
                    ]
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec();
        
        match pspec {
            Ok(pspec) => println!("{}", pspec.pprint()),
            Err(errors) => {
                for error in errors {
                    println!("-----\n{}", error);
                }
            }
        }
    }

    #[test]
    fn test_encode_outlives() {
        let spec_json = r#"
        {
            "objects": [
                {
                    "name": "foo"
                }
            ],
            "endpoints": [
                {
                    "name": "test_func",
                    "variables": [
                        {
                            "name": "obj1",
                            "type": "foo",
                            "dir": "*"
                        },
                        {
                            "name": "obj2",
                            "type": "foo",
                            "dir": "*"
                        }
                    ],
                    "template": "test_func(a, b);",
                    "outlives": [
                        {
                            "a": "obj1",
                            "b": "obj2"
                        }
                    ]
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        
        println!("{}", pspec.pprint());
    }
}
