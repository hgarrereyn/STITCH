use gf_synth::{harness_writer::HarnessWriter, spec::{EndpointSpec, HeaderSpec, Spec, TypeSpec}};


#[test]
fn test_add() {
    let spec = Spec {
        types: vec![
            TypeSpec { name: "Foo".to_string() }
        ],
        endpoints: vec![
            EndpointSpec {
                name: "Foo()".to_string(),
                inputs: vec![],
                outputs: vec![0],
                args: vec![],
                template: "$o0 = new Foo();".to_string()
            },
            EndpointSpec {
                name: "Foo::write(char val)".to_string(),
                inputs: vec![0],
                outputs: vec![0],
                args: vec!["char".to_string()],
                template: "$i0->write($a0);\n$o0 = $i0;".to_string()
            },
            EndpointSpec {
                name: "Foo::check()".to_string(),
                inputs: vec![0],
                outputs: vec![0],
                args: vec![],
                template: "$i0->check();\n$o0 = $i0;".to_string()
            },
            EndpointSpec {
                name: "~Foo()".to_string(),
                inputs: vec![0],
                outputs: vec![],
                args: vec![],
                template: "delete $i0;".to_string()
            }
        ],
        headers: vec![
            HeaderSpec::CppHeader("lib.h".to_string())
        ]
    };

    let writer = HarnessWriter::new(&spec);
    let code = writer.write_harness();

    println!("{}", code);
}
