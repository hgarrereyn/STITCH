use core::panic;
use std::{ffi::c_void, io::Write};

use crate::graph::Graph;

use super::graph::NodeRefType;


extern "C" {
    // Defined in the fuzz target generated code.
    fn GRAPHFUZZ_TARGET_INFO(exec_shim_ptr: &mut *const c_void, write_shim_ptr: &mut *const c_void, shim_size_ptr: &mut *const c_void, num_shims: &mut usize, write_init_shim_ptr: &mut *const c_void);
    fn GRAPHFUZZ_INVOKE_SHIM(shim: *const c_void, in_ref: *const *const c_void, out_ref: *const *const c_void, context: *const u8, bailed: &mut usize);
    fn GRAPHFUZZ_PRE_INIT();
    fn GRAPHFUZZ_POST_INIT();
}

type WriteInitShim = unsafe fn();
type Shim = unsafe fn(in_ref: *const *const c_void, out_ref: *const *const c_void, context: *const u8);

pub struct Harness {
    exec_shims: Vec<Shim>,
    write_shims: Vec<Shim>,
    pub shim_sizes: Vec<u32>,
    write_init_shim: WriteInitShim,
}

#[derive(Debug)]
pub enum InvokeResult {
    Success,
    Bailed(BailPoint),
}

#[derive(Debug)]
pub struct BailPoint {
    pub node_idx: usize,
    pub node_type: usize,
}

impl Harness {
    pub const MAX_SLOTS: usize = 20;

    pub fn new() -> Self {
        let mut exec_shim_ptr: *const c_void = std::ptr::null();
        let mut write_shim_ptr: *const c_void = std::ptr::null();
        let mut shim_size_ptr: *const c_void = std::ptr::null();
        let mut write_init_shim_ptr: *const c_void = std::ptr::null();
        let mut num_shims: usize = 0;
        unsafe {
            GRAPHFUZZ_TARGET_INFO(&mut exec_shim_ptr, &mut write_shim_ptr, &mut shim_size_ptr, &mut num_shims, &mut write_init_shim_ptr);
        }

        let exec_shims = unsafe { std::slice::from_raw_parts(exec_shim_ptr as *const Shim, num_shims).to_vec() };
        let write_shims = unsafe { std::slice::from_raw_parts(write_shim_ptr as *const Shim, num_shims).to_vec() };
        let shim_sizes = unsafe { std::slice::from_raw_parts(shim_size_ptr as *const u32, num_shims).to_vec() };
        let write_init_shim = unsafe { std::mem::transmute(write_init_shim_ptr) };

        Self {
            exec_shims,
            write_shims,
            shim_sizes,
            write_init_shim
        }
    }

    pub fn invoke(&self, graph: &Graph) -> InvokeResult {
        let slots = vec![vec![std::ptr::null::<c_void>(); Harness::MAX_SLOTS]; graph.nodes.len()];
        
        let order = graph.topological_sort();

        unsafe {
            // println!("Invoking pre init");
            GRAPHFUZZ_PRE_INIT();
            // println!("Invoked pre init");
        }

        for idx in order.iter() {
            let node = &graph.nodes[*idx];
            let in_ref = node.in_ref.iter().map(
                |x| match x {
                    NodeRefType::Connected(ref y) => slots[y.node_idx][y.conn_idx],
                    NodeRefType::Disconnected => panic!("Disconnected node reference"),
                }).collect::<Vec<_>>();
            let out_ref = slots[node.index].as_ptr();

            // (self.exec_shims[node.typ])(in_ref.as_ptr(), out_ref, node.context.as_ptr());

            let mut m = 0;
            unsafe {
                // println!("Invoking shim");
                GRAPHFUZZ_INVOKE_SHIM(self.exec_shims[node.typ] as *const c_void, in_ref.as_ptr(), out_ref, node.context.as_ptr(), &mut m);
                // println!("Invoked shim");
            }
            if m != 0 {
                return InvokeResult::Bailed(BailPoint {
                    node_idx: node.index,
                    node_type: node.typ,
                });
            }
        }

        unsafe {
            // println!("Invoking post init");
            GRAPHFUZZ_POST_INIT();
            // println!("Invoked post init");
        }

        InvokeResult::Success
    }

    pub fn write(&self, graph: &Graph) {
        // Call the write init shim first to output headers
        unsafe {
            (self.write_init_shim)();
        }

        // Emit main function start
        println!("int main() {{");

        // flush stdout
        std::io::stdout().flush().unwrap();

        let mut slots = vec![vec![std::ptr::null::<c_void>(); Harness::MAX_SLOTS]; graph.nodes.len()];
        
        let order = graph.topological_sort();

        let mut vidx = 0;

        for idx in order.iter() {
            let node = &graph.nodes[*idx];
            let in_ref = node.in_ref.iter().map(
                |x| match x {
                    NodeRefType::Connected(ref y) => slots[y.node_idx][y.conn_idx],
                    NodeRefType::Disconnected => panic!("Disconnected node reference"),
                }).collect::<Vec<_>>();
            let out_ref = slots[node.index].as_ptr();

            // Fill out ref with new indices
            for (i, _output) in node.out_ref.iter().enumerate() {
                slots[node.index][i] = vidx as *const c_void;
                vidx += 1;
            }

            unsafe {
                (self.write_shims[node.typ])(in_ref.as_ptr(), out_ref, node.context.as_ptr());
            }
        }

        // Emit main function end
        println!("    return 0;");
        println!("}}");

        // flush stdout
        std::io::stdout().flush().unwrap();
    }
}

