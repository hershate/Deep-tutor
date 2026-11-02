pub mod commands;
pub mod error;
pub mod store;

use std::sync::Mutex;

pub struct BackendState {
    pub store: Mutex<store::Store>,
}
