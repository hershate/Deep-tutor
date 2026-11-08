#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod startup;

use backend::{
    agent, backup, commands, community, engagement, export, knowledge, learning, maintenance,
    materials, notes, planning, plugins, practice, projects, providers, reports, review, security,
    sharing, sync, web, BackendState,
};
use tauri::Manager;

fn main() {
    let verify_native = std::env::args_os().any(|argument| argument == "--verify-native");
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        // 37#4/37#8：官方 opener——「在资源管理器中显示」与 https 外链显式
        // 走系统浏览器（替代 WebView2 NewWindowRequested 的未定义行为）
        .plugin(tauri_plugin_opener::init())
        .setup(move |app| {
            let (data_root, default_root, pointer, configuration_error) = if verify_native {
                let root = std::env::var_os("DEEPTUTOR_VERIFY_DATA_DIR")
                    .map(std::path::PathBuf::from)
                    .filter(|path| path.is_absolute())
                    .ok_or(
                        "DEEPTUTOR_VERIFY_DATA_DIR must be an absolute verification directory",
                    )?;
                let pointer = root.join("verification-storage.json");
                (root.clone(), root.clone(), pointer, None)
            } else {
                let default_root = app.path().app_data_dir()?;
                let pointer = app
                    .path()
                    .app_local_data_dir()?
                    .join("storage-location.json");
                let selected = startup::configured_directory(&pointer, &default_root);
                let root = selected.clone().unwrap_or_else(|_| default_root.clone());
                (root, default_root, pointer, selected.err())
            };
            if verify_native {
                app.handle()
                    .manage(security::SecurityState::for_verification(
                        &data_root.join("deep-tutor.db"),
                    ));
                startup::initialize(
                    app.handle(),
                    &data_root,
                    &default_root,
                    &pointer,
                    configuration_error,
                );
            } else {
                startup::initialize_background(
                    app.handle(),
                    &data_root,
                    &default_root,
                    &pointer,
                    configuration_error,
                );
            }
            if verify_native {
                startup::ensure_ready(app.handle())
                    .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
                let snapshot = app
                    .state::<BackendState>()
                    .store
                    .lock()
                    .map_err(|_| -> Box<dyn std::error::Error> {
                        "verification database lock failed".into()
                    })?
                    .bootstrap()
                    .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
                assert!(snapshot["tasks"].is_array() && snapshot["preferences"].is_object());
                println!(
                    "native verification passed: workspace={}, lastEventId={}",
                    snapshot["workspaceId"], snapshot["lastEventId"]
                );
                app.handle().exit(0);
            }
            Ok(())
        })
        .invoke_handler(security::guard_handler(tauri::generate_handler![
            startup::commands::get_storage_overview,
            startup::commands::retry_storage_initialization,
            startup::commands::set_storage_location,
            startup::commands::reset_storage_location,
            startup::commands::restart_application,
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
            // 21#1：PAT 登录入口已接入安全面板（verify 通过即签发会话）
            security::commands::authenticate_access_token,
            security::commands::set_security_quota,
            materials::commands::import_material_files,
            materials::commands::import_material_archive,
            materials::commands::import_material_text,
            materials::commands::list_materials,
            materials::commands::get_material,
            materials::commands::get_material_media_info,
            materials::commands::read_material_media_chunk,
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
            providers::commands::export_provider_config_file,
            providers::commands::import_provider_deep_link,
            providers::commands::test_provider,
            providers::commands::stream_provider_chat,
            // 21#2：stream_provider_chat_with_tools 前端零调用（Agent 工具流量
            // 经 route_provider_chat 在 Rust 内部直调），从注册表移除收敛 IPC 面
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
            providers::commands::export_usage_csv_file,
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
            practice::commands::export_practice_mistakes_file,
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
            plugins::commands::save_plugin_asset,
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
        ]));
    let mut context = tauri::generate_context!();
    if verify_native {
        context.config_mut().app.windows.clear();
    }
    builder
        .run(context)
        .expect("error while running the Deep Tutor application");
}
