use crate::graph::Graph;
use crate::schema::Schema;
use std::fs;
use std::path::Path;
use std::collections::HashSet;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use std::ffi::CString;

// Include the base filter module content at compile time
const BASE_FILTER_CODE: &str = include_str!("filter.py");

#[derive(Debug)]
pub struct Filter {
    path: String,
    module: Py<PyModule>,
    filter_fn: PyObject,
}

impl Filter {
    pub fn new(path: &Path) -> Result<Filter, Box<dyn std::error::Error>> {
        let module_name = path.file_stem()
            .and_then(|s| s.to_str())
            .ok_or("Invalid module name")?;
        
        // Read the user's filter code
        let user_filter_code = fs::read_to_string(path)?;
        
        // Combine the code
        let combined_code = format!("{}\n{}", BASE_FILTER_CODE, user_filter_code);
        
        let module_name = CString::new(module_name)?;
        let module_code = CString::new(combined_code)?;

        Python::with_gil(|py| {
            // Import the filter module that contains TestCase definition
            let filter_module = PyModule::from_code(py, module_code.as_c_str(), module_name.as_c_str(), module_name.as_c_str())?;
            let filter_fn: PyObject = filter_module.getattr("graphfuzz_filter")?.into();
            Ok(Self {
                path: path.to_str().unwrap_or("").to_string(),
                module: filter_module.into(),
                filter_fn,
            })
        })
    }

    pub fn test(&self, py: Python<'_>, graph: &Graph, schema: &Schema) -> Result<bool, Box<dyn std::error::Error>> {
        match graph.to_testcase_py_object(py, schema) {
            Ok(py_testcase) => {
                match self.filter_fn.call1(py, (py_testcase,)) {
                    Ok(result) => Ok(result.extract::<bool>(py)?),
                    Err(e) => {
                        eprintln!("Error calling filter function: {}", e);
                        Err(e.into())
                    }
                }
            },
            Err(e) => {
                eprintln!("Error converting graph to Python TestCase object: {}", e);
                Err(e.into())
            }
        }
    }
}

// Manages hotloading filters given a directory of filter files
pub struct FilterManager {
    filter_dir: String,
    filters: Vec<Filter>,
    loaded_paths: HashSet<String>,
}

impl FilterManager {
    pub fn new(filter_dir: String) -> Result<Self, Box<dyn std::error::Error>> {
        // Initialize Python interpreter
        pyo3::prepare_freethreaded_python();
        Ok(Self {
            filter_dir,
            filters: Vec::new(),
            loaded_paths: HashSet::new(),
        })
    }

    pub fn reload(&mut self, verbose: bool) {
        // Get all .py files from the filter directory
        let mut current_paths = HashSet::new();
        
        if let Ok(entries) = fs::read_dir(&self.filter_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().map_or(false, |ext| ext == "py") {
                    if let Some(path_str) = path.to_str() {
                        current_paths.insert(path_str.to_string());
                        
                        // Only load if we haven't seen this file before
                        if !self.loaded_paths.contains(path_str) {
                            if verbose {
                                println!("Loading filter: {}", path_str);
                            }
                            
                            match Filter::new(&path) {
                                Ok(filter) => {
                                    self.filters.push(filter);
                                    self.loaded_paths.insert(path_str.to_string());
                                }
                                Err(e) => {
                                    if verbose {
                                        eprintln!("Failed to load filter {}: {}", path_str, e);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        
        // Remove filters whose files no longer exist
        self.filters.retain(|filter| {
            let keep = current_paths.contains(&filter.path);
            if !keep && verbose {
                println!("Removing filter: {}", filter.path);
            }
            keep
        });
        
        self.loaded_paths = current_paths;
    }

    pub fn test(&self, graph: &Graph, schema: &Schema) -> Result<bool, Box<dyn std::error::Error>> {
        Python::with_gil(|py| {
            let mut result = true;
            for filter in self.filters.iter() {
                result = result && filter.test(py, graph, schema)?;
            }
            Ok(result)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn create_test_graph() -> Graph {
        let mut graph = Graph { nodes: Vec::new() };
        
        // Add a simple graph with 3 nodes in different layers
        graph.add_node(0, 0, 0, 1, 0); // Node 0: output only
        graph.add_node(1, 1, 1, 1, 0); // Node 1: input and output
        graph.add_node(2, 2, 1, 0, 0); // Node 2: input only
        
        // Connect nodes: 0 -> 1 -> 2
        graph.link(0, 0, 1, 0);
        graph.link(1, 0, 2, 0);
        
        graph
    }

    fn create_test_schema() -> Schema {
        Schema::default() // For testing purposes, we'll use a default schema
    }

    #[test]
    fn test_filter_basic() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = TempDir::new()?;
        let filter_path = temp_dir.path().join("test_filter.py");

        println!("filter_path: {}", filter_path.to_string_lossy());
        
        // Create a simple filter that checks if testcase has exactly 3 endpoints
        fs::write(&filter_path, r#"
def graphfuzz_filter(testcase):
    return len(testcase.endpoints) == 3
"#)?;
        
        // Initialize Python and create filter
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            println!("py: {:?}", py.version());
            let filter = Filter::new(&filter_path).unwrap();
            let schema = create_test_schema();
            
            println!("filter: {:?}", filter);
            // Test with a valid graph
            let graph = create_test_graph();
            assert!(filter.test(py, &graph, &schema).unwrap());
            
            // Test with an invalid graph (add another node)
            let mut invalid_graph = graph;
            invalid_graph.add_node(3, 3, 0, 0, 0);
            assert!(!filter.test(py, &invalid_graph, &schema).unwrap());
        });

        Ok(())
    }

    #[test]
    fn test_filter_node_properties() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = TempDir::new()?;
        let filter_path = temp_dir.path().join("test_filter.py");
        
        // Create a filter that checks endpoint properties
        fs::write(&filter_path, r#"
def graphfuzz_filter(testcase):
    # Check if execution order is valid
    for i, endpoint_idx in enumerate(testcase.execution_order):
        endpoint = testcase.endpoints[endpoint_idx]
        # Check all inputs come from earlier in execution order
        for input_ref in endpoint.inputs:
            if input_ref.endpoint_idx >= endpoint_idx:
                return False
    return True
"#)?;
        
        // Initialize Python and create filter
        pyo3::prepare_freethreaded_python();
        let filter = Filter::new(&filter_path)?;
        let schema = create_test_schema();
        
        // Test with a valid graph (proper execution order)
        Python::with_gil(|py| {
            let graph = create_test_graph();
            assert!(filter.test(py, &graph, &schema).unwrap());
        });
        
        // Test with an invalid graph (improper execution order)
        let mut invalid_graph = Graph { nodes: Vec::new() };
        invalid_graph.add_node(0, 2, 0, 1, 0); // Higher layer outputs to lower layer
        invalid_graph.add_node(1, 1, 1, 0, 0);
        invalid_graph.link(0, 0, 1, 0); // This violates execution order
        Python::with_gil(|py| {
            assert!(!filter.test(py, &invalid_graph, &schema).unwrap());
        });
        
        Ok(())
    }

    #[test]
    fn test_filter_manager() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = TempDir::new()?;
        let schema = create_test_schema();
        
        // Create two filters
        fs::write(temp_dir.path().join("filter1.py"), r#"
def graphfuzz_filter(testcase):
    return len(testcase.endpoints) <= 3
"#)?;
        
        fs::write(temp_dir.path().join("filter2.py"), r#"
def graphfuzz_filter(testcase):
    return all(len(endpoint.inputs) <= 2 for endpoint in testcase.endpoints)
"#)?;
        
        // Create filter manager
        let mut manager = FilterManager::new(temp_dir.path().to_string_lossy().to_string())?;
        manager.reload(false);
        
        // Test with valid graph (3 nodes, max 1 input)
        let graph = create_test_graph();
        assert!(manager.test(&graph, &schema).unwrap());
        
        // Test with invalid graph (4 nodes)
        let mut invalid_graph1 = graph.clone();
        invalid_graph1.add_node(3, 3, 0, 0, 0);
        assert!(!manager.test(&invalid_graph1, &schema).unwrap());
        
        // Test with invalid graph (3 inputs)
        let mut invalid_graph2 = Graph { nodes: Vec::new() };
        invalid_graph2.add_node(0, 0, 3, 0, 0); // Node with 3 inputs
        assert!(!manager.test(&invalid_graph2, &schema).unwrap());
        
        Ok(())
    }

    #[test]
    fn test_filter_manager_hotreload() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = TempDir::new()?;
        let filter1_path = temp_dir.path().join("dynamic_filter1.py");
        let filter2_path = temp_dir.path().join("dynamic_filter2.py");
        let schema = create_test_schema();
        
        // Create initial filter
        fs::write(&filter1_path, r#"
def graphfuzz_filter(testcase):
    return len(testcase.endpoints) <= 3
"#)?;
        
        // Create filter manager and load initial filter
        let mut manager = FilterManager::new(temp_dir.path().to_string_lossy().to_string())?;
        manager.reload(false);
        
        // Test with 3-node graph (should pass)
        let graph = create_test_graph();
        assert!(manager.test(&graph, &schema).unwrap());
        
        fs::write(&filter2_path, r#"
def graphfuzz_filter(testcase):
    return len(testcase.endpoints) <= 2
"#)?;
        
        // Reload filters
        manager.reload(false);
        
        // Same 3-node graph should now fail
        assert!(!manager.test(&graph, &schema).unwrap());
        
        Ok(())
    }
}
