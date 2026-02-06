use bytemuck::{Pod, Zeroable};


#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
pub struct EndpointStats {
    pub success_count: usize,
    pub failure_count: usize,
    pub skipped_count: usize,
    pub executions: usize,
    pub average_millis: f64,
}

impl EndpointStats {
    pub fn mark_success(&mut self, millis: f64) {
        self.success_count += 1;
        self.executions += 1;
        self.average_millis = (self.average_millis * (self.executions - 1) as f64 + millis) / self.executions as f64;
        // println!("Marking success, success_count: {}, failure_count: {}, executions: {}, average_millis: {}", self.success_count, self.failure_count, self.executions, self.average_millis);
    }

    pub fn mark_failure(&mut self) {
        self.failure_count += 1;
        self.executions += 1;
        // println!("Marking failure, success_count: {}, failure_count: {}, executions: {}, average_millis: {}", self.success_count, self.failure_count, self.executions, self.average_millis);
    }

    /// Mark that this endpoint was *scheduled* to run but skipped because an earlier
    /// endpoint in the graph failed. This should not affect the execution count or
    /// average runtime, since the endpoint never actually ran.
    pub fn mark_skipped(&mut self) {
        self.skipped_count += 1;
    }
}

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
pub struct RuntimeMetadata {
    num_endpoints: usize,
    // rest of data is EndpointStats
}

impl RuntimeMetadata {
    pub fn compute_size(num_endpoints: usize) -> usize {
        std::mem::size_of::<Self>() + num_endpoints * std::mem::size_of::<EndpointStats>()
    }
}

pub struct RuntimeMetadataView<'a> {
    backing: &'a mut [u8],
}

impl<'a> RuntimeMetadataView<'a> {
    pub fn new(backing: &'a mut [u8]) -> Self {
        Self { backing }
    }

    pub fn metadata(&self) -> &RuntimeMetadata {
        unsafe {
            &*(self.backing as *const [u8] as *const RuntimeMetadata)
        }
    }

    pub fn endpoint_stats(&self, endpoint_idx: usize) -> &EndpointStats {
        unsafe {
            &*(self.backing.as_ptr().add(std::mem::size_of::<RuntimeMetadata>() + endpoint_idx * std::mem::size_of::<EndpointStats>()) as *const EndpointStats)
        }
    }

    pub fn endpoint_stats_mut(&mut self, endpoint_idx: usize) -> &mut EndpointStats {
        unsafe {
            &mut *(self.backing.as_mut_ptr().add(std::mem::size_of::<RuntimeMetadata>() + endpoint_idx * std::mem::size_of::<EndpointStats>()) as *mut EndpointStats)
        }
    }
}
