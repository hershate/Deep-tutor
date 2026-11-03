#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use backend::{
    agent, commands, community, knowledge, learning, materials, plugins, practice, providers,
    review, sharing, store::Store, BackendState,
};
use std::sync::Mutex;
use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let path = app.path().app_data_dir()?.join("deep-tutor.db");
            let store = Store::new(&path)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            let material_state = materials::MaterialState::new(&path)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            let provider_state = providers::ProviderState::new(&path)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            let learning_state =
                learning::LearningState::new(&path, &provider_state, &material_state);
            let knowledge_state =
                knowledge::KnowledgeState::new(&path, &provider_state, &material_state);
            let agent_state = agent::AgentState::new(&path, &provider_state, &learning_state);
            let practice_state =
                practice::PracticeState::new(&path, &provider_state, &learning_state);
            let review_state = review::ReviewState::new(&path, &learning_state);
            let sharing_state = sharing::SharingState::new(&learning_state);
            app.manage(BackendState {
                store: Mutex::new(store),
                materials: material_state,
                providers: provider_state,
                learning: learning_state,
                agent: agent_state,
                practice: practice_state,
                review: review_state,
                knowledge: knowledge_state,
                community: community::CommunityState::new(&path),
                plugins: plugins::PluginState::new(&path)
                    .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?,
                sharing: sharing_state,
            });
            let state = app.state::<BackendState>();
            materials::recover(app.handle(), &state.materials)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            providers::recover_chats(app.handle(), &state.providers)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            providers::routing::recover(app.handle(), &state.providers)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            learning::recover(app.handle(), &state.learning)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            agent::recover(app.handle(), &state.agent)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            practice::recover(app.handle(), &state.practice)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            knowledge::recover(app.handle(), &state.knowledge)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::bootstrap,
            commands::get_app_state,
            commands::get_collection,
            commands::replace_collection,
            commands::save_draft,
            commands::get_draft,
            commands::delete_draft,
            commands::reset_application,
            commands::export_backup,
            commands::preview_backup,
            commands::import_backup,
            commands::events_since,
            materials::commands::import_material_files,
            materials::commands::import_material_text,
            materials::commands::list_materials,
            materials::commands::get_material,
            materials::commands::enqueue_material_parse,
            materials::commands::cancel_material_parse,
            materials::commands::delete_material,
            materials::commands::export_material_original,
            materials::commands::export_material_text,
            providers::commands::list_provider_profiles,
            providers::commands::get_active_provider,
            providers::commands::upsert_provider_profile,
            providers::commands::set_active_provider,
            providers::commands::delete_provider_profile,
            providers::commands::export_provider_config,
            providers::commands::import_provider_deep_link,
            providers::commands::test_provider,
            providers::commands::stream_provider_chat,
            providers::commands::stream_provider_chat_with_tools,
            providers::commands::cancel_provider_chat,
            providers::commands::get_provider_chat,
            providers::commands::get_routing_settings,
            providers::commands::save_routing_settings,
            providers::commands::list_provider_route_logs,
            providers::commands::list_provider_circuits,
            providers::commands::route_provider_chat,
            providers::commands::get_usage_overview,
            providers::commands::save_usage_settings,
            providers::commands::upsert_usage_rate,
            providers::commands::export_usage_csv,
            learning::commands::start_learning_plan,
            learning::commands::list_learning_plans,
            learning::commands::get_learning_plan,
            learning::commands::cancel_learning_plan,
            learning::commands::resume_learning_plan,
            learning::commands::adopt_learning_plan,
            learning::commands::regenerate_learning_plan,
            learning::commands::get_knowledge_overview,
            learning::commands::get_knowledge_path,
            learning::commands::add_knowledge_task,
            learning::commands::get_reading_progress,
            learning::commands::set_reading_progress,
            learning::commands::get_lecture,
            learning::commands::generate_lecture,
            learning::commands::cancel_lecture,
            learning::commands::save_study_note,
            sharing::commands::create_shared_plan,
            sharing::commands::list_shared_plans,
            sharing::commands::read_shared_plan,
            sharing::commands::revoke_shared_plan,
            sharing::commands::read_shared_plan_package,
            sharing::commands::export_shared_plan_package,
            sharing::commands::read_shared_plan_file,
            review::commands::create_review_card,
            review::commands::update_review_card,
            review::commands::delete_review_card,
            review::commands::set_review_card_suspended,
            review::commands::list_review_cards,
            review::commands::next_review_card,
            review::commands::grade_review_card,
            review::commands::get_review_stats,
            review::commands::list_review_history,
            practice::commands::generate_practice,
            practice::commands::get_practice_set,
            practice::commands::list_practice_history,
            practice::commands::cancel_practice_generation,
            practice::commands::resume_practice_generation,
            practice::commands::submit_practice_answer,
            practice::commands::submit_exam,
            practice::commands::list_practice_mistakes,
            practice::commands::export_practice_mistakes,
            practice::commands::set_practice_bookmark,
            practice::commands::set_practice_mistake_mastered,
            practice::commands::cancel_practice_answer,
            practice::commands::resume_practice_answer,
            knowledge::commands::search_knowledge,
            knowledge::commands::ask_knowledge,
            knowledge::commands::list_knowledge_sessions,
            knowledge::commands::get_knowledge_session,
            knowledge::commands::cancel_knowledge_answer,
            knowledge::commands::resume_knowledge_answer,
            knowledge::commands::delete_knowledge_session,
            knowledge::commands::clear_knowledge_history,
            community::commands::get_community_overview,
            community::commands::create_community_post,
            community::commands::reply_community_post,
            community::commands::accept_community_reply,
            community::commands::toggle_community_reaction,
            community::commands::create_community_group,
            community::commands::toggle_community_group,
            community::commands::create_community_room,
            community::commands::reserve_community_room,
            community::commands::cancel_community_reservation,
            community::commands::cancel_community_room,
            community::commands::claim_community_reminders,
            plugins::commands::install_plugin,
            plugins::commands::set_plugin_enabled,
            plugins::commands::reload_plugin,
            plugins::commands::uninstall_plugin,
            plugins::commands::emit_plugin_ping,
            plugins::commands::get_plugin_overview,
            plugins::commands::list_plugin_events,
            plugins::commands::get_developer_asset,
            agent::commands::create_agent_session,
            agent::commands::list_agent_sessions,
            agent::commands::get_agent_session,
            agent::commands::send_agent_message,
            agent::commands::get_agent_run,
            agent::commands::cancel_agent_run,
            agent::commands::resume_agent_run,
            agent::commands::delete_agent_session,
            agent::commands::approve_agent_request,
            agent::commands::cancel_agent_approval,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Deep Tutor application");
}
