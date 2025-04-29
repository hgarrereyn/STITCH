use ouroboros::self_referencing;
use smtlib::{backend::cvc5_binary::Cvc5Binary, Backend, Solver, Storage};

use crate::pspec::PSpec;
use crate::sampler::sgraph::SGraph;

use super::sampler::{Sampler, SamplerConfig};

pub struct MultiSampler<'st> {
    pub pspec: PSpec,
    pub config: SamplerConfig,
    pub storage: &'st Storage,
    pub sampler: Sampler<'st>,
    pub solver: Solver<'st, Cvc5Binary>,

    // Keep track of the generated graphs
    pub generated: Vec<SGraph>,
}

#[derive(Debug, Clone)]
pub enum SamplerConstraint {
    IncludeFunction(usize),
}

impl<'st> MultiSampler<'st> {
    pub fn new(pspec: &PSpec, config: SamplerConfig, storage: &'st Storage) -> Self {
        let sampler = Sampler::new(pspec.clone(), storage, config.clone());
        let solver = Solver::new(&storage, Cvc5Binary::new("cvc5").unwrap()).unwrap();

        MultiSampler {
            pspec: pspec.clone(),
            config,
            storage,
            sampler,
            solver,
            generated: vec![],
        }
    }

    pub fn initialize(&mut self) {
        self.sampler.setup(&mut self.solver);
    }

    pub fn sample_with_constraints(&mut self, constraints: &[SamplerConstraint]) -> Option<SGraph> {
        let graph = self.solver.scope(|solver| {
            // Apply temporary constraints inside scope
            for constraint in constraints {
                match constraint {
                    SamplerConstraint::IncludeFunction(func_idx) => {
                        self.sampler.require_func_active(solver, *func_idx);
                    }
                }
            }

            // Sample graph
            if let Some(graph) = self.sampler.sample(solver, false) {
                Ok(graph)
            } else {
                Err(smtlib::Error::Smt(String::from("No graph found"), String::from("")))
            }
        });

        if let Ok(graph) = graph {
            self.generated.push(graph.clone());
            self.sampler.exclude_graph(&mut self.solver, &graph);
            Some(graph)
        } else {
            None
        }
    }

    pub fn sample_one(&mut self) -> Option<SGraph> {
        let graph = self.sampler.sample(&mut self.solver, false).unwrap();
        self.generated.push(graph.clone());
        self.sampler.exclude_graph(&mut self.solver, &graph);
        Some(graph)
    }

    pub fn sample_many(&mut self, n: usize) {
        for _ in 0..n {
            self.sample_one();
        }
    }
}


#[self_referencing]
pub struct MultiSamplerContainer {
    pub storage: Storage,
    #[borrows(storage)]
    #[covariant]
    pub multisampler: MultiSampler<'this>,
}

impl MultiSamplerContainer {
    pub fn create(pspec: &PSpec, config: SamplerConfig) -> Self {
        let storage = Storage::new();
        MultiSamplerContainerBuilder {
            storage,
            multisampler_builder: |storage| MultiSampler::new(pspec, config, storage),
        }.build()
    }

    pub fn initialize(&mut self) {
        self.with_multisampler_mut(|ms| ms.initialize());
    }

    pub fn sample_one(&mut self) -> Option<SGraph> {
        self.with_multisampler_mut(|ms| ms.sample_one())
    }

    pub fn sample_many(&mut self, n: usize) {
        self.with_multisampler_mut(|ms| ms.sample_many(n));
    }

    pub fn sample_with_constraints(&mut self, constraints: &[SamplerConstraint]) -> Option<SGraph> {
        self.with_multisampler_mut(|ms| ms.sample_with_constraints(constraints))
    }
}

#[cfg(test)]
mod tests {
    use crate::spec::Spec;

    use super::*;

    #[test]
    fn test_multisampler_sample_one() {
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

        let storage = Storage::new();
        let config = SamplerConfig {
            max_calls: 10,
            ..Default::default()
        };
        let mut multisampler = MultiSampler::new(&pspec, config, &storage);
        multisampler.initialize();
        multisampler.sample_many(100);
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

        let storage = Storage::new();
        let config = SamplerConfig {
            max_calls: 1,
            ..Default::default()
        };
        let mut multisampler = MultiSampler::new(&pspec, config, &storage);
        multisampler.initialize();
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
}
