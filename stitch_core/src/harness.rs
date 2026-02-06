use core::panic;
use std::{ffi::c_void, io::Write, time::Instant};

use crate::{graph::Graph, runtime_metadata::RuntimeMetadataView};

use super::graph::NodeRefType;

extern "C" {
    // Defined in the fuzz target generated code.
    fn STITCH_TARGET_INFO(
        exec_shim_ptr: &mut *const c_void,
        write_shim_ptr: &mut *const c_void,
        shim_size_ptr: &mut *const c_void,
        shim_arg_offsets_ptr: &mut *const c_void,
        num_shims: &mut usize,
        write_init_shim_ptr: &mut *const c_void,
    );
    fn STITCH_INVOKE_SHIM(
        shim: *const c_void,
        in_ref: *const *const c_void,
        out_ref: *const *const c_void,
        context: *const u8,
        bailed: &mut usize,
        exit_buffer: *mut u8,
        exit_buffer_size: usize,
    );
    fn STITCH_PRE_INIT();
    fn STITCH_POST_INIT();
}

type WriteInitShim = unsafe fn();
type Shim =
    unsafe fn(in_ref: *const *const c_void, out_ref: *const *const c_void, context: *const u8);

pub struct Harness {
    exec_shims: Vec<Shim>,
    write_shims: Vec<Shim>,
    pub shim_sizes: Vec<u32>,
    pub shim_arg_offsets: Vec<Vec<u32>>,
    write_init_shim: WriteInitShim,
}

#[derive(Debug, Clone)]
pub enum InvokeResult {
    Success,
    Bailed(BailPoint),                     // FUZZ_BAIL() was called
    AssertionViolation(BailPoint, String), // FUZZ_ASSERT() violation
    LibraryException(BailPoint, String),   // Library exception
}

#[derive(Debug, Clone)]
pub struct BailPoint {
    pub node_idx: usize,
    pub node_type: usize,
}

/// Simplified structure for tracking bail information in shared memory
#[derive(Debug, Clone, Copy)]
#[repr(C, packed)]
pub struct BailInfo {
    pub bailed: bool,
    pub node_idx: usize,
    pub node_type: usize,
}

impl InvokeResult {
    /// Convert InvokeResult to simplified BailInfo for efficient shared memory storage
    pub fn to_bail_info(&self) -> BailInfo {
        match self {
            InvokeResult::Success => BailInfo {
                bailed: false,
                node_idx: 0,
                node_type: 0,
            },
            InvokeResult::Bailed(bail_point) => BailInfo {
                bailed: true,
                node_idx: bail_point.node_idx,
                node_type: bail_point.node_type,
            },
            InvokeResult::AssertionViolation(bail_point, _) => BailInfo {
                bailed: true,
                node_idx: bail_point.node_idx,
                node_type: bail_point.node_type,
            },
            InvokeResult::LibraryException(bail_point, _) => BailInfo {
                bailed: true,
                node_idx: bail_point.node_idx,
                node_type: bail_point.node_type,
            },
        }
    }
}

impl Harness {
    pub const MAX_SLOTS: usize = 20;

    pub fn new() -> Self {
        let mut exec_shim_ptr: *const c_void = std::ptr::null();
        let mut write_shim_ptr: *const c_void = std::ptr::null();
        let mut shim_size_ptr: *const c_void = std::ptr::null();
        let mut shim_arg_offsets_ptr: *const c_void = std::ptr::null();
        let mut write_init_shim_ptr: *const c_void = std::ptr::null();
        let mut num_shims: usize = 0;
        unsafe {
            STITCH_TARGET_INFO(
                &mut exec_shim_ptr,
                &mut write_shim_ptr,
                &mut shim_size_ptr,
                &mut shim_arg_offsets_ptr,
                &mut num_shims,
                &mut write_init_shim_ptr,
            );
        }

        let exec_shims =
            unsafe { std::slice::from_raw_parts(exec_shim_ptr as *const Shim, num_shims).to_vec() };
        let write_shims = unsafe {
            std::slice::from_raw_parts(write_shim_ptr as *const Shim, num_shims).to_vec()
        };
        let shim_sizes =
            unsafe { std::slice::from_raw_parts(shim_size_ptr as *const u32, num_shims).to_vec() };
        let shim_arg_offsets_ptrs = unsafe {
            std::slice::from_raw_parts(shim_arg_offsets_ptr as *const *const u32, num_shims)
                .to_vec()
        };
        let write_init_shim = unsafe { std::mem::transmute(write_init_shim_ptr) };

        let mut shim_arg_offsets = Vec::new();
        for i in 0..num_shims {
            let mut offsets = Vec::new();
            let mut offset_ptr = shim_arg_offsets_ptrs[i];
            while unsafe { *offset_ptr != u32::MAX } {
                offsets.push(unsafe { *offset_ptr });
                offset_ptr = unsafe { offset_ptr.add(1) };
            }
            shim_arg_offsets.push(offsets);
        }

        Self {
            exec_shims,
            write_shims,
            shim_sizes,
            shim_arg_offsets,
            write_init_shim,
        }
    }

    pub fn invoke(
        &self,
        graph: &Graph,
        mut metadata: Option<&mut RuntimeMetadataView>,
    ) -> InvokeResult {
        // Flattened slot storage: [node_idx * MAX_SLOTS + conn_idx].
        // This avoids per-node Vec allocations on every invocation.
        let slots =
            vec![std::ptr::null::<c_void>(); Harness::MAX_SLOTS * graph.nodes.len()];

        let order = graph.get_invocation_order();

        unsafe {
            STITCH_PRE_INIT();
        }

        for idx in order.iter() {
            let node = &graph.nodes[*idx];
            let in_ref = node
                .in_ref
                .iter()
                .map(|x| match x {
                    NodeRefType::Connected(ref y) => {
                        slots[y.node_idx * Harness::MAX_SLOTS + y.conn_idx]
                    }
                    NodeRefType::Disconnected => panic!("Disconnected node reference"),
                })
                .collect::<Vec<_>>();
            let out_ref = unsafe {
                slots
                    .as_ptr()
                    .add(node.index * Harness::MAX_SLOTS)
            };

            let mut exit_type = 0;
            let mut exit_buffer = [0; 1024];

            let start = Instant::now();
            unsafe {
                STITCH_INVOKE_SHIM(
                    self.exec_shims[node.typ] as *const c_void,
                    in_ref.as_ptr(),
                    out_ref,
                    node.context.as_ptr(),
                    &mut exit_type,
                    exit_buffer.as_mut_ptr(),
                    1024,
                );
            }
            let end = Instant::now();
            let millis = end.duration_since(start).as_millis() as f64;

            match exit_type {
                0 => {
                    // Normal exit
                    if let Some(metadata) = &mut metadata {
                        metadata.endpoint_stats_mut(node.typ).mark_success(millis);
                    }
                }
                1 => {
                    // Libary exception
                    unsafe {
                        STITCH_POST_INIT();
                    }
                    return InvokeResult::LibraryException(
                        BailPoint {
                            node_idx: node.index,
                            node_type: node.typ,
                        },
                        String::from_utf8_lossy(
                            &exit_buffer[..exit_buffer.iter().position(|&x| x == 0).unwrap()],
                        )
                        .to_string(),
                    );
                }
                2 => {
                    // FUZZ_BAIL() was called
                    if let Some(metadata) = &mut metadata {
                        metadata.endpoint_stats_mut(node.typ).mark_failure();
                    }
                    unsafe {
                        STITCH_POST_INIT();
                    }
                    return InvokeResult::Bailed(BailPoint {
                        node_idx: node.index,
                        node_type: node.typ,
                    });
                }
                3 => {
                    // FUZZ_ASSERT() violation
                    unsafe {
                        STITCH_POST_INIT();
                    }
                    return InvokeResult::AssertionViolation(
                        BailPoint {
                            node_idx: node.index,
                            node_type: node.typ,
                        },
                        String::from_utf8_lossy(
                            &exit_buffer[..exit_buffer.iter().position(|&x| x == 0).unwrap()],
                        )
                        .to_string(),
                    );
                }
                _ => {
                    panic!("Invalid exit type: {}", exit_type);
                }
            }
        }

        unsafe {
            STITCH_POST_INIT();
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

        let mut slots =
            vec![std::ptr::null::<c_void>(); Harness::MAX_SLOTS * graph.nodes.len()];

        let order = graph.topological_sort();

        let mut vidx = 0;

        for idx in order.iter() {
            let node = &graph.nodes[*idx];
            let in_ref = node
                .in_ref
                .iter()
                .map(|x| match x {
                    NodeRefType::Connected(ref y) => {
                        slots[y.node_idx * Harness::MAX_SLOTS + y.conn_idx]
                    }
                    NodeRefType::Disconnected => panic!("Disconnected node reference"),
                })
                .collect::<Vec<_>>();
            let out_ref = unsafe {
                slots
                    .as_ptr()
                    .add(node.index * Harness::MAX_SLOTS)
            };

            // Fill out ref with new indices
            for (i, _output) in node.out_ref.iter().enumerate() {
                slots[node.index * Harness::MAX_SLOTS + i] = vidx as *const c_void;
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
