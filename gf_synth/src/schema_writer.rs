use gf_core::schema::{Endpoint, Schema, Type};

use crate::inner_spec::InnerSpec;


pub struct SchemaWriter<'a> {
    spec: &'a InnerSpec
}

impl<'a> SchemaWriter<'a> {
    pub fn new(spec: &InnerSpec) -> SchemaWriter {
        SchemaWriter { spec }
    }

    pub fn write_schema(&self) -> String {
        let mut schema = Schema {
            types: vec![],
            endpoints: vec![],
        };

        for (i, type_spec) in self.spec.types.iter().enumerate() {
            schema.types.push(Type {
                name: type_spec.name.clone(),
                idx: i,
            });
        }

        for (i, endpoint) in self.spec.endpoints.iter().enumerate() {
            let mut inputs = vec![];
            for input in &endpoint.inputs {
                inputs.push(input.clone());
            }

            let mut outputs = vec![];
            for output in &endpoint.outputs {
                outputs.push(output.clone());
            }

            schema.endpoints.push(Endpoint {
                name: endpoint.name.clone(),
                inputs,
                outputs,
                idx: i,
                context_size: None,
            });
        }

        serde_json::to_string(&schema).unwrap()
    }
}
