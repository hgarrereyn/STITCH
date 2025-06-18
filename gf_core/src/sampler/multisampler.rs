use rustsat::{instances::{BasicVarManager, Cnf, SatInstance}, solvers::SolverResult};
use rustsat_cadical::CaDiCaL;
use rustsat::solvers::Solve;

use crate::pspec::PSpec;
use crate::sampler::sgraph::SGraph;

use super::encoder::{Encoder, EncoderConfig};

pub struct MultiSampler {
    pub pspec: PSpec,
    pub encoder: Encoder,

    // Base encoding of the PSpec
    base_encoding: Cnf,
    base_encoding_vm: BasicVarManager,

    // Main incremental solver
    main_solver: CaDiCaL<'static, 'static>,
    main_solver_vm: BasicVarManager,

    // Keep track of the generated graphs
    pub generated: Vec<SGraph>,
}

#[derive(Debug, Clone)]
pub enum SamplerConstraint {
    IncludeFunction(usize),
}

impl MultiSampler {
    pub fn new(pspec: &PSpec, config: EncoderConfig) -> Self {
        // Initialize the encoder and base enc
        let (encoder, base_encoding, base_encoding_vm) = Encoder::create(pspec.clone(), config.clone());

        let mut main_solver = CaDiCaL::default();
        main_solver.add_cnf(base_encoding.clone()).unwrap();

        MultiSampler {
            pspec: pspec.clone(),
            encoder,
            base_encoding,
            base_encoding_vm: base_encoding_vm.clone(),
            main_solver,
            main_solver_vm: base_encoding_vm,
            generated: vec![],
        }
    }

    fn track_generated(&mut self, graph: &SGraph) {
        self.generated.push(graph.clone());

        // Add blocking clauses
        let mut blocking_encoding = SatInstance::new_with_manager(self.main_solver_vm.clone());
        self.encoder.encode_blocking(&mut blocking_encoding, graph);
        let (blocking_cnf, blocking_vm) = blocking_encoding.into_cnf();
        self.main_solver.add_cnf(blocking_cnf).unwrap();
        self.main_solver_vm = blocking_vm;
    }

    fn _sample_one(solver: &mut CaDiCaL<'static, 'static>, encoder: &Encoder) -> Option<SGraph> {
        match solver.solve() {
            Ok(SolverResult::Sat) => {
                let assignment = solver.full_solution().unwrap();
                let graph = encoder.decode_sgraph(&assignment, false);
                encoder.debug_print(&assignment);
                Some(graph)
            }
            _ => {
                println!("Unsat");
                None
            }
        }
    }

    pub fn sample_one(&mut self) -> Option<SGraph> {
        let graph = Self::_sample_one(&mut self.main_solver, &self.encoder);
        if let Some(graph) = &graph {
            self.track_generated(graph);
        }
        graph
    }

    pub fn sample_many(&mut self, n: usize) -> Vec<SGraph> {
        let mut graphs = vec![];
        for _ in 0..n {
            if let Some(graph) = self.sample_one() {
                graphs.push(graph);
            } else {
                break;
            }
        }
        graphs
    }

    pub fn sample_with_constraints(&mut self, constraints: &[SamplerConstraint]) -> Option<SGraph> {
        let mut multi_encoding = SatInstance::new_with_manager(self.base_encoding_vm.clone());

        for graph in self.generated.iter() {
            self.encoder.encode_blocking(&mut multi_encoding, graph);
        }

        for constraint in constraints {
            match constraint {
                SamplerConstraint::IncludeFunction(func_idx) => {
                    self.encoder.encode_func_active(&mut multi_encoding, *func_idx);
                }
            }
        }

        let mut tmp_solver = CaDiCaL::default();
        tmp_solver.add_cnf(self.base_encoding.clone()).unwrap();
        tmp_solver.add_cnf(multi_encoding.clone().into_cnf().0).unwrap();

        let graph = Self::_sample_one(&mut tmp_solver, &self.encoder);
        if let Some(graph) = &graph {
            self.track_generated(graph);
        }
        graph
    }
}


#[cfg(test)]
mod tests {
    use crate::spec::Spec;

    use super::*;

    fn sample_spec(pspec: &PSpec, config: EncoderConfig, require_func_active: usize) -> SGraph {
        let mut multisampler = MultiSampler::new(pspec, config);
        multisampler.sample_with_constraints(&[SamplerConstraint::IncludeFunction(require_func_active)]).unwrap()
    }

    fn new_pspec(spec_json: &str) -> PSpec {
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        spec.build_pspec().unwrap()
    }

    #[test]
    fn test_multisampler_sample_many() {
        let spec_json = r#"
        {
            "objects": [
                {
                    "name": "foo",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "int"
                        }
                    ]
                }
            ],
            "endpoints": [
                {
                    "name": "init_lib",
                    "variables": [],
                    "template": "init_lib();",
                    "role": "library_initializer"
                },   
                {
                    "name": "new_foo",
                    "variables": [{ "name": "z", "type": "foo", "dir": "new" }],
                    "template": "z = new_foo();",
                    "preconditions": [],
                    "effects": [
                        "z.x := 0"
                    ]
                },
                {
                    "name": "inc_foo",
                    "variables": [{ "name": "z", "type": "foo", "dir": "*" }],
                    "template": "inc_foo(z);",
                    "effects": [
                        "z.x := z.x + 1"
                    ]
                },
                {
                    "name": "dec_foo",
                    "variables": [{ "name": "z", "type": "foo", "dir": "*" }],
                    "template": "dec_foo(z);",
                    "effects": [
                        "z.x := z.x - 1"
                    ]
                },
                {
                    "name": "del_foo",
                    "variables": [{ "name": "z", "type": "foo", "dir": "del" }],
                    "template": "del_foo(z);",
                    "preconditions": [
                        "z.x == 0"
                    ],
                    "effects": []
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();

        let config = EncoderConfig {
            max_calls: 10,
            ..Default::default()
        };
        let mut multisampler = MultiSampler::new(&pspec, config);
        multisampler.sample_many(1000);
    }

    #[test]
    fn test_sample_with_constraints() {
        let spec_json = r#"
        {
            "objects": [],
            "endpoints": [
                {
                    "name": "fn_0",
                    "template": "fn_0();"
                },   
                {
                    "name": "fn_1",
                    "template": "fn_1();"
                },
                {
                    "name": "fn_2",
                    "template": "fn_2();"
                },
                {
                    "name": "fn_3",
                    "template": "fn_3();"
                },
                {
                    "name": "fn_4",
                    "template": "fn_4();"
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();

        let config = EncoderConfig {
            max_calls: 1,
            ..Default::default()
        };
        let mut multisampler = MultiSampler::new(&pspec, config);
        for i in 0..5 {
            if i == 2 {
                continue;
            }
            let graph = multisampler.sample_with_constraints(&[SamplerConstraint::IncludeFunction(i)]);
            let graph = graph.unwrap();
            assert_eq!(graph.nodes.len(), 1);
            assert_eq!(graph.nodes[0].func_idx, i);
        }

        let graph = multisampler.sample_one();
        let graph = graph.unwrap();
        assert_eq!(graph.nodes.len(), 1);
        assert_eq!(graph.nodes[0].func_idx, 2);
    }

    #[test]
    fn test_sampler_single() {
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
                }
            ],
            "endpoints": [
                {
                    "name": "init_foo",
                    "variables": [{ "name": "z", "type": "foo", "dir": "new" }],
                    "template": "z = init_foo();",
                    "preconditions": [],
                    "effects": [
                        "z.x := 0",
                        "z.y := A"
                    ]
                },   
                {
                    "name": "use_foo",
                    "variables": [{ "name": "z", "type": "foo", "dir": "*" }],
                    "template": "use_foo(z);",
                    "preconditions": [],
                    "effects": [
                        "z.x := z.x + 1"
                    ]
                },
                {
                    "name": "switch_foo",
                    "variables": [{ "name": "z", "type": "foo", "dir": "*" }],
                    "template": "switch_foo(z);",
                    "preconditions": [
                        "z.x == 2"
                    ],
                    "effects": [
                        "z.y := B"
                    ]
                },
                {
                    "name": "del_foo",
                    "variables": [{ "name": "z", "type": "foo", "dir": "del" }],
                    "template": "del_foo(z);",
                    "preconditions": [
                        "z.x == 3",
                        "z.y is B"
                    ],
                    "effects": []
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        println!("{}", pspec.pprint());

        let sgraph = sample_spec(&pspec, EncoderConfig::default(), 0);
        sgraph.to_graph(&pspec, 0).pprint();
    }

    #[test]
    fn test_sampler_multi() {
        let spec_json = r#"
        {
            "objects": [
                {
                    "name": "number",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "int"
                        },
                        {
                            "name": "state",
                            "type": "enum",
                            "variants": ["FULL", "EMPTY"]
                        }
                    ]
                }
            ],
            "endpoints": [
                {
                    "name": "new_number",
                    "variables": [{ "name": "z", "type": "number", "dir": "new" }],
                    "template": "z = new_number();",
                    "preconditions": [],
                    "effects": [
                        "z.x := 1",
                        "z.state := FULL"
                    ]
                },   
                {
                    "name": "merge_number",
                    "variables": [
                        { "name": "z", "type": "number", "dir": "*" },
                        { "name": "w", "type": "number", "dir": "*" }
                    ],
                    "template": "merge_number(z, w);",
                    "preconditions": [],
                    "effects": [
                        "z.x := z.x + w.x",
                        "z.state := FULL",
                        "w.x := 0",
                        "w.state := EMPTY"
                    ]
                },
                {
                    "name": "discard_number",
                    "variables": [{ "name": "z", "type": "number", "dir": "del" }],
                    "template": "discard_number(z);",
                    "preconditions": [
                        "z.state is EMPTY"
                    ],
                    "effects": []
                },
                {
                    "name": "submit_number",
                    "variables": [{ "name": "z", "type": "number", "dir": "del" }],
                    "template": "submit_number(z);",
                    "preconditions": [
                        "z.x == 4",
                        "z.state is FULL"
                    ],
                    "effects": []
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        println!("{}", pspec.pprint());

        let sgraph = sample_spec(&pspec, EncoderConfig {
            max_calls: 20,
            max_args: 2,
            instances_per_object: 5,
        }, 0);
        sgraph.to_graph(&pspec, 0).pprint();
    }

    #[test]
    fn test_sampler_outlives() {
        let spec1 = r#"
        {
            "objects": [
                { "name": "foo" },
                { "name": "bar" }
            ],
            "endpoints": [
                {
                    "name": "new_a",
                    "variables": [{ "name": "a", "type": "foo", "dir": "new" }],
                    "template": "a = new_a();"
                },
                {
                    "name": "new_b",
                    "variables": [{ "name": "b", "type": "bar", "dir": "new" }],
                    "template": "b = new_b();"
                },
                {
                    "name": "use",
                    "variables": [
                        { "name": "a", "type": "foo", "dir": "*" },
                        { "name": "b", "type": "bar", "dir": "*" }
                    ],
                    "template": "use(a, b);",
                    "outlives": [
                        { "a": "a", "b": "b" }
                    ]
                },
                {
                    "name": "del_a",
                    "variables": [{ "name": "a", "type": "foo", "dir": "del" }],
                    "template": "del_a(a);"
                },
                {
                    "name": "del_b",
                    "variables": [{ "name": "b", "type": "bar", "dir": "del" }],
                    "template": "del_b(b);"
                }
            ]
        }
        "#;
        let spec2 = r#"
        {
            "objects": [
                { "name": "foo" },
                { "name": "bar" }
            ],
            "endpoints": [
                {
                    "name": "new_a",
                    "variables": [{ "name": "a", "type": "foo", "dir": "new" }],
                    "template": "a = new_a();"
                },
                {
                    "name": "new_b",
                    "variables": [{ "name": "b", "type": "bar", "dir": "new" }],
                    "template": "b = new_b();"
                },
                {
                    "name": "use",
                    "variables": [
                        { "name": "a", "type": "foo", "dir": "*" },
                        { "name": "b", "type": "bar", "dir": "*" }
                    ],
                    "template": "use(a, b);",
                    "outlives": [
                        { "a": "b", "b": "a" }
                    ]
                },
                {
                    "name": "del_a",
                    "variables": [{ "name": "a", "type": "foo", "dir": "del" }],
                    "template": "del_a(a);"
                },
                {
                    "name": "del_b",
                    "variables": [{ "name": "b", "type": "bar", "dir": "del" }],
                    "template": "del_b(b);"
                }
            ]
        }
        "#;

        let pspec1 = new_pspec(spec1);
        let pspec2 = new_pspec(spec2);

        let config = EncoderConfig {
            max_calls: 5,
            max_args: 2,
            instances_per_object: 2,
        };

        let spec1 = sample_spec(&pspec1, config.clone(), 2);
        let spec2 = sample_spec(&pspec2, config.clone(), 2);

        let graph1 = spec1.to_graph(&pspec1, 0);
        let graph2 = spec2.to_graph(&pspec2, 0);

        let typ1 = graph1.nodes.iter().map(|n| n.typ).collect::<Vec<_>>();
        let typ2 = graph2.nodes.iter().map(|n| n.typ).collect::<Vec<_>>();

        assert_eq!(typ1[2..], vec![2,4,3]);
        assert_eq!(typ2[2..], vec![2,3,4]);
    }

    #[test]
    fn test_sampler_initializer() {
        let spec1 = r#"
        {
            "objects": [
                { "name": "foo" }
            ],
            "endpoints": [
                {
                    "name": "init",
                    "variables": [],
                    "template": "init();",
                    "role": "library_initializer"
                },
                {
                    "name": "new_a",
                    "variables": [{ "name": "a", "type": "foo", "dir": "new" }],
                    "template": "a = new_a();"
                },
                {
                    "name": "del_a",
                    "variables": [{ "name": "a", "type": "foo", "dir": "del" }],
                    "template": "del_a(a);"
                }
            ]
        }
        "#;

        let pspec1 = new_pspec(spec1);

        let config = EncoderConfig {
            max_calls: 5,
            max_args: 2,
            instances_per_object: 2,
        };

        let spec1 = sample_spec(&pspec1, config.clone(), 2);
        let graph1 = spec1.to_graph(&pspec1, 0);

        // Ensure that it starts with init
        assert_eq!(graph1.nodes[0].typ, 0);
    }
}
