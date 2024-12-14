use crate::spec::{EndpointSpec, HeaderSpec, Spec};

static HELPER_CODE: &str = include_str!("helper.h");

pub struct HarnessWriter<'a> {
    spec: &'a Spec
}

impl<'a> HarnessWriter<'a> {
    pub fn new(spec: &Spec) -> HarnessWriter {
        HarnessWriter { spec }
    }

    pub fn write_harness(&self) -> String {
        let mut code = String::new();

        // Builtin headers
        code.push_str("#include <cstdio>\n");
        code.push_str("#include <cstdlib>\n");
        code.push_str("#include <cstdint>\n");

        self.emit_headers(&mut code, &self.spec.headers);

        self.emit_helpers(&mut code);

        for (idx, endpoint) in self.spec.endpoints.iter().enumerate() {
            self.emit_exec_endpoint(&mut code, idx, endpoint);
        }

        for (idx, endpoint) in self.spec.endpoints.iter().enumerate() {
            self.emit_write_endpoint(&mut code, idx, endpoint);
        }

        for (idx, endpoint) in self.spec.endpoints.iter().enumerate() {
            self.emit_context_size_info(&mut code, idx, endpoint);
        }

        self.emit_linkage_info(&mut code);

        code
    }

    fn emit_exec_endpoint(&self, code: &mut String, idx: usize, endpoint: &EndpointSpec) {
        let shim_name = format!("exec_shim_{}", idx);
        
        // Replace template variables with references.
        let mut template = endpoint.template.clone();
        for (i, input) in endpoint.inputs.iter().enumerate().rev() {
            let typ = &self.spec.types[*input].name;
            template = template.replace(&format!("$i{}", i), &format!("(({} *)in_ref[{}])", typ, i));
        }
        for (i, _output) in endpoint.outputs.iter().enumerate().rev() {
            template = template.replace(&format!("$o{}", i), &format!("out_ref[{}]", i));
        }
        for (i, _arg) in endpoint.args.iter().enumerate().rev() {
            template = template.replace(&format!("$a{}", i), &format!("__arg_{}", i));
        }

        // Instantiate arguments.
        let mut arg_instantiations = String::new();
        for (i, arg) in endpoint.args.iter().enumerate() {
            arg_instantiations.push_str(&format!("using __arg_{}_type = {};\n", i, arg));
            arg_instantiations.push_str(&format!("__arg_{}_type __arg_{};\n", i, i));
            arg_instantiations.push_str(&format!("std::memcpy(&__arg_{}, context, sizeof({}));\n", i, arg));
            arg_instantiations.push_str(&format!("context += sizeof({});\n", arg));
        }

        code.push_str(&format!("extern \"C\" void {}(void **in_ref, void **out_ref, char *context) {{\n", shim_name));
        code.push_str(&arg_instantiations);
        code.push_str("// ----- TEMPLATE CODE -----\n");
        code.push_str(&format!("{}\n", template));
        code.push_str("// -------------------------\n");
        code.push_str("}\n\n");
    }

    // The write endpoint is like the exec endpoint except invoking it prints equivalent code to stdout instead of executing it.
    fn emit_write_endpoint(&self, code: &mut String, idx: usize, endpoint: &EndpointSpec) {
        let shim_name = format!("write_shim_{}", idx);

        // Space for output variables.
        let mut variable_definitions = String::new();
        for (i, output) in endpoint.outputs.iter().enumerate() {
            let typ = &self.spec.types[*output].name;
            variable_definitions.push_str(&format!("printf(\"{} *var_%td;\\n\", (intptr_t)out_ref[{}]);\n", typ, i));
        }

        let mut template = printf_escape(&endpoint.template);

        // Replace template variables with references.
        for (i, _input) in endpoint.inputs.iter().enumerate().rev() {
            template = template.replace(&format!("$i{}", i), &format!("var_%{}$td", 1 + i));
        }
        for (i, _output) in endpoint.outputs.iter().enumerate().rev() {
            template = template.replace(&format!("$o{}", i), &format!("var_%{}$td", 1 + i + endpoint.inputs.len()));
        }
        for (i, _arg) in endpoint.args.iter().enumerate().rev() {
            template = template.replace(&format!("$a{}", i), &format!("__arg_{}", i));
        }

        let mut template_args = String::new();
        let args = endpoint.inputs.iter().map(|x| {
            format!("(intptr_t)in_ref[{}]", x)
        }).chain(endpoint.outputs.iter().map(|x| {
            format!("(intptr_t)out_ref[{}]", x)
        })).collect::<Vec<_>>();
        template_args.push_str(&args.join(", "));

        // Instantiate arguments.
        let mut arg_instantiations = String::new();
        for (i, arg) in endpoint.args.iter().enumerate() {
            // arg_instantiations.push_str(&format!("auto __arg_{} = deserialize_from_bytes<{}>(context);\n", i, arg));
            arg_instantiations.push_str(&format!("using __arg_{}_type = {};\n", i, arg));
            arg_instantiations.push_str(&format!("__arg_{}_type __arg_{};\n", i, i));
            arg_instantiations.push_str(&format!("std::memcpy(&__arg_{}, context, sizeof({}));\n", i, arg));
            arg_instantiations.push_str(&format!("context += sizeof({});\n", arg));
        }

        let mut print_arg_instantiations = String::new();
        for (i, arg) in endpoint.args.iter().enumerate() {
            print_arg_instantiations.push_str(&format!("printf(\"using __arg_{}_type = {};\\n\");\n", i, arg));
            print_arg_instantiations.push_str(&format!("printf(\"__arg_{}_type __arg_{} = %s;\\n\", generate_initializer(__arg_{}).c_str());\n", i, i, i));
        }

        code.push_str(&format!("extern \"C\" void {}(void **in_ref, void **out_ref, char *context) {{\n", shim_name));
        code.push_str(&arg_instantiations);
        code.push_str("// ----- TEMPLATE CODE -----\n");
        code.push_str(&variable_definitions);
        code.push_str(&format!("printf(\"{{ // start {}\\n\");\n", endpoint.name));
        code.push_str(&print_arg_instantiations);
        code.push_str(&format!("printf(\"{}\\n\", {});\n", template, template_args));
        code.push_str(&format!("printf(\"}} // end {}\\n\");\n", endpoint.name));
        code.push_str("// -------------------------\n");
        code.push_str("}\n\n");
    }

    fn emit_context_size_info(&self, code: &mut String, idx: usize, endpoint: &EndpointSpec) {
        let context_size = if endpoint.args.len() > 0 {
            endpoint.args.iter().map(|x| {
                format!("sizeof({})", x)
            }).collect::<Vec<_>>().join(" + ")
        } else {
            "0".to_string()
        };
        code.push_str(&format!("const unsigned int shim_size_{} = {};\n", idx, context_size));
    }

    fn emit_linkage_info(&self, code: &mut String) {
        code.push_str("extern \"C\" {\n");
        code.push_str("    void (*GRAPHFUZZ_EXEC_SHIMS[])(void **, void **, char *) = {\n");
        for (idx, _endpoint) in self.spec.endpoints.iter().enumerate() {
            code.push_str(&format!("        exec_shim_{},\n", idx));
        }
        code.push_str("    };\n\n");

        code.push_str("    void (*GRAPHFUZZ_WRITE_SHIMS[])(void **, void **, char *) = {\n");
        for (idx, _endpoint) in self.spec.endpoints.iter().enumerate() {
            code.push_str(&format!("        write_shim_{},\n", idx));
        }
        code.push_str("    };\n\n");

        code.push_str("    unsigned int GRAPHFUZZ_SHIM_SIZES[] = {\n");
        for (idx, _endpoint) in self.spec.endpoints.iter().enumerate() {
            code.push_str(&format!("        shim_size_{},\n", idx));
        }
        code.push_str("    };\n\n");

        code.push_str("    const unsigned int GRAPHFUZZ_SHIMS_COUNT = ");
        code.push_str(&format!("{};\n\n", self.spec.endpoints.len()));

        code.push_str("    void GRAPHFUZZ_TARGET_INFO(void **exec_shim_ptr, void **write_shim_ptr, unsigned int **shim_size_ptr, unsigned int *shim_count) {\n");
        code.push_str("        *exec_shim_ptr = GRAPHFUZZ_EXEC_SHIMS;\n");
        code.push_str("        *write_shim_ptr = GRAPHFUZZ_WRITE_SHIMS;\n");
        code.push_str("        *shim_size_ptr = GRAPHFUZZ_SHIM_SIZES;\n");
        code.push_str("        *shim_count = GRAPHFUZZ_SHIMS_COUNT;\n");
        code.push_str("    }\n");
        code.push_str("}\n");
    }

    fn emit_headers(&self, code: &mut String, headers: &[HeaderSpec]) {
        for header in headers {
            match header {
                HeaderSpec::CHeader(header) => {
                    code.push_str(&format!("extern \"C\" {{ {} }}\n", format_header(header)));
                }
                HeaderSpec::CppHeader(header) => {
                    code.push_str(&format!("{}\n", format_header(header)));
                }
            }
        }
        code.push_str("\n");
    }

    fn emit_helpers(&self, code: &mut String) {
        code.push_str("// ----- HELPER CODE -----\n");
        code.push_str(HELPER_CODE);
        code.push_str("// -------------------------\n");
    }
}

fn format_header(header: &str) -> String {
    // check if system or user header
    if header.contains(".") {
        format!("#include \"{}\"", header)
    } else {
        format!("#include <{}>", header)
    }
}

fn printf_escape(s: &str) -> String {
    s.replace("%", "%%")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\"", "\\\"")
}
