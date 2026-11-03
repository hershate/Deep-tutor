pub mod agent;
pub mod commands;
pub mod community;
pub mod error;
pub mod knowledge;
pub mod learning;
pub mod materials;
pub mod practice;
pub mod plugins;
pub mod providers;
pub mod review;
pub mod store;

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
}
