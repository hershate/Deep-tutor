pub mod commands;
pub mod error;
pub mod materials;
pub mod providers;
pub mod store;

use std::sync::Mutex;

pub struct BackendState {
    pub store: Mutex<store::Store>,
    pub materials: materials::MaterialState,
    pub providers: providers::ProviderState,
}
