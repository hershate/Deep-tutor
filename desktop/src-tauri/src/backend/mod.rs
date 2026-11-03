pub mod agent;
pub mod backup;
pub mod commands;
pub mod community;
pub mod error;
pub mod export;
pub mod knowledge;
pub mod learning;
pub mod maintenance;
pub mod materials;
pub mod plugins;
pub mod practice;
pub mod providers;
pub(crate) mod random;
pub mod reports;
pub mod review;
pub mod security;
pub mod sharing;
pub mod store;
pub mod sync;

use std::sync::Mutex;

pub struct BackendState {
    pub store: Mutex<store::Store>,
    pub materials: materials::MaterialState,
    pub providers: providers::ProviderState,
    pub learning: learning::LearningState,
    pub agent: agent::AgentState,
    pub practice: practice::PracticeState,
    pub review: review::ReviewState,
    pub knowledge: knowledge::KnowledgeState,
    pub community: community::CommunityState,
    pub plugins: plugins::PluginState,
    pub sharing: sharing::SharingState,
}
