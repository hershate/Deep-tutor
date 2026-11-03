#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use backend::{
    agent, backup, commands, community, engagement, export, knowledge, learning, maintenance,
    materials, notes, planning, plugins, practice, projects, providers, reports, review, security,
    sharing, store::Store, sync, web, BackendState,
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
            app.manage(security::SecurityState::new(&path));
            let principal = match security::initialize_session(
                app.handle(),
                &app.state::<security::SecurityState>(),
            ) {
                Ok(principal) => Some(principal),
                Err(error) if security::reauthentication_required(&error) => None,
                Err(error) => return Err(error.into()),
            };
            let state = app.state::<BackendState>();
            if let Some(principal) = principal {
                security::with_user(principal.id, || -> backend::error::Result<()> {
                    *state.store.lock().map_err(|_| {
                        backend::error::BackendError::new("store_unavailable", "本地数据层暂不可用")
                    })? = Store::new(&path)?;
                    materials::recover(app.handle(), &state.materials)?;
                    providers::recover_chats(app.handle(), &state.providers)?;
                    providers::routing::recover(app.handle(), &state.providers)?;
                    learning::recover(app.handle(), &state.learning)?;
                    agent::recover(app.handle(), &state.agent)?;
                    practice::recover(app.handle(), &state.practice)?;
                    projects::recover(app.handle(), &projects::ProjectState::new(&state.learning))?;
                    knowledge::recover(app.handle(), &state.knowledge)?;
                    sync::recover(app.handle(), &sync::SyncService::new(&state.learning))?;
                    Ok(())
                })
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
                security::with_user(principal.id, || {
                    sync::scheduler::start_scheduler(app.handle().clone())
                });
            }
            Ok(())
        })
        .invoke_handler(security::guard_handler(tauri::generate_handler![
            commands::bootstrap,
            commands::get_app_state,
            commands::get_collection,
            commands::replace_collection,
            commands::save_draft,
            commands::get_draft,
            commands::delete_draft,
            commands::reset_application,
            commands::events_since,
            backup::commands::export_full_backup,
            backup::commands::preview_full_backup,
            backup::commands::import_full_backup,
            sync::commands::get_sync_overview,
            sync::commands::save_sync_config,
            sync::commands::start_sync_job,
            sync::commands::get_sync_job,
            sync::commands::cancel_sync_job,
            sync::commands::get_sync_versions,
            sync::commands::restore_sync_version,
            maintenance::commands::get_maintenance_report,
            maintenance::commands::cleanup_orphan_files,
            maintenance::commands::vacuum_database,
            reports::commands::get_learning_report,
            reports::commands::get_learning_calendar,
            reports::commands::get_focus_overview,
            reports::commands::start_focus_session,
            reports::commands::complete_focus_session,
            reports::commands::pause_focus_session,
            reports::commands::resume_focus_session,
            reports::commands::cancel_focus_session,
            reports::commands::save_study_checkin,
            reports::commands::adopt_report_recommendations,
            reports::commands::export_learning_report,
            engagement::commands::search_workspace,
            engagement::commands::get_notifications,
            engagement::commands::mark_all_notifications_read,
            engagement::commands::mark_notification_read,
            engagement::commands::get_learning_profile,
            engagement::commands::save_learning_profile,
            engagement::commands::restart_learning_diagnostic,
            engagement::commands::get_dashboard,
            planning::commands::get_plan_overview,
            planning::commands::get_plan_alternatives,
            planning::commands::apply_plan_intensity,
            planning::commands::update_learning_goal,
            planning::commands::assign_task_date,
            security::commands::get_security_overview,
            security::commands::login_local_user,
            security::commands::logout_local_user,
            security::commands::create_local_user,
            security::commands::set_local_user_active,
            security::commands::remove_local_user,
            security::commands::create_access_token,
            security::commands::revoke_access_token,
            security::commands::authenticate_access_token,
            security::commands::set_security_quota,
            materials::commands::import_material_files,
            materials::commands::import_material_text,
            materials::commands::list_materials,
            materials::commands::get_material,
            materials::commands::enqueue_material_parse,
            materials::commands::cancel_material_parse,
            materials::commands::delete_material,
            materials::commands::export_material_original,
            materials::commands::export_material_text,
            notes::commands::import_notes,
            notes::commands::get_library_overview,
            web::commands::fetch_webpage,
            web::commands::import_webpage,
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
            projects::commands::get_project_workspace,
            projects::commands::submit_project_version,
            projects::commands::cancel_project_feedback,
            projects::commands::resume_project_feedback,
            projects::commands::save_project_iteration_note,
            export::commands::export_content,
            export::commands::export_notes_archive,
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
        ]))
        .run(tauri::generate_context!())
        .expect("error while running the Deep Tutor application");
}
