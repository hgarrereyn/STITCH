use core::panic;
use std::ffi::c_void;

use crate::graph::Graph;

use super::graph::NodeRefType;


extern "C" {
    fn GRAPHFUZZ_TARGET_INFO(exec_shim_ptr: &mut *const c_void, write_shim_ptr: &mut *const c_void, shim_size_ptr: &mut *const c_void, num_shims: &mut usize);

    fn GRAPHFUZZ_INVOKE_SHIM(shim: *const c_void, in_ref: *const *const c_void, out_ref: *const *const c_void, context: *const u8, bailed: &mut usize);
}


type Shim = unsafe fn(in_ref: *const *const c_void, out_ref: *const *const c_void, context: *const u8);

pub struct Harness {
    exec_shims: Vec<Shim>,
    write_shims: Vec<Shim>,
    pub shim_sizes: Vec<u32>,
}

impl Harness {
    pub const MAX_SLOTS: usize = 20;

    pub fn new() -> Self {
        let mut exec_shim_ptr: *const c_void = std::ptr::null();
        let mut write_shim_ptr: *const c_void = std::ptr::null();
        let mut shim_size_ptr: *const c_void = std::ptr::null();
        let mut num_shims: usize = 0;
        unsafe {
            GRAPHFUZZ_TARGET_INFO(&mut exec_shim_ptr, &mut write_shim_ptr, &mut shim_size_ptr, &mut num_shims);
        }

        let exec_shims = unsafe { std::slice::from_raw_parts(exec_shim_ptr as *const Shim, num_shims).to_vec() };
        let write_shims = unsafe { std::slice::from_raw_parts(write_shim_ptr as *const Shim, num_shims).to_vec() };
        let shim_sizes = unsafe { std::slice::from_raw_parts(shim_size_ptr as *const u32, num_shims).to_vec() };

        Self {
            exec_shims,
            write_shims,
            shim_sizes
        }
    }

    pub fn invoke(&self, graph: &Graph) {
        let slots = vec![vec![std::ptr::null::<c_void>(); Harness::MAX_SLOTS]; graph.nodes.len()];
        
        let order = graph.topological_sort();

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
                GRAPHFUZZ_INVOKE_SHIM(self.exec_shims[node.typ] as *const c_void, in_ref.as_ptr(), out_ref, node.context.as_ptr(), &mut m);
            }
            if m != 0 {
                return;
            }
        }
    }

    pub fn write(&self, graph: &Graph) {
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
    }
}
