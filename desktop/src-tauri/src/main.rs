#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod startup;

use backend::{
    agent, backup, commands, community, engagement, export, knowledge, learning, maintenance,
    materials, notes, planning, plugins, practice, projects, providers, reports, review, security,
    sharing, sync, web, BackendState,
};
use tauri::Manager;

/// 应用装配：resolve 数据目录 → 初始化后端 → verify 模式下自检并 exit(0)。
/// 错误以 Result 交还调用方（verify 模式转 exit(2)，GUI 模式沿 setup 上抛
/// 走 initialize_background 的失败恢复 UI）。
fn assemble(app: &mut tauri::App, verify_native: bool) -> Result<(), Box<dyn std::error::Error>> {
    let (data_root, default_root, pointer, configuration_error) = if verify_native {
        let root = std::env::var_os("DEEPTUTOR_VERIFY_DATA_DIR")
            .map(std::path::PathBuf::from)
            .filter(|path| path.is_absolute())
            .ok_or("DEEPTUTOR_VERIFY_DATA_DIR must be an absolute verification directory")?;
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
}

/// 46#1：单实例防护——双开触发跨进程重复同步上传（远端最后写者胜、空耗
/// 版本保留额度）与莫名其妙的 revision_conflict 提示。命名互斥体快速失败
/// 并聚焦已有主窗口；不引入 tauri-plugin-single-instance 依赖（仓库规约
/// 默认不新增依赖，kernel32/user32 直连即可）。verify 通道无窗口且按目录
/// 隔离，不占用全局互斥体。
#[cfg(windows)]
mod single_instance {
    const ERROR_ALREADY_EXISTS: u32 = 183;
    const SW_RESTORE: i32 = 9;

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateMutexW(
            attributes: *mut core::ffi::c_void,
            initial_owner: i32,
            name: *const u16,
        ) -> *mut core::ffi::c_void;
        fn GetLastError() -> u32;
    }

    #[link(name = "user32")]
    extern "system" {
        fn FindWindowW(class: *const u16, title: *const u16) -> *mut core::ffi::c_void;
        fn ShowWindow(window: *mut core::ffi::c_void, command: i32) -> i32;
        fn SetForegroundWindow(window: *mut core::ffi::c_void) -> i32;
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain([0]).collect()
    }

    /// 返回 false 表示已有实例持有互斥体（句柄故意泄漏给进程生命周期保管）。
    pub fn acquire() -> bool {
        let name = wide("DeepTutor-SingleInstance-7f3a9c2e");
        unsafe {
            let handle = CreateMutexW(std::ptr::null_mut(), 0, name.as_ptr());
            // 句柄为 NULL 仅在极端分配失败——按可启动处理，不阻塞用户
            !handle.is_null() && GetLastError() != ERROR_ALREADY_EXISTS
        }
    }

    /// 把已有实例的主窗口带到前台（找不到窗口时静默退出第二实例）。
    pub fn focus_existing(title: &str) {
        let title = wide(title);
        unsafe {
            let window = FindWindowW(std::ptr::null(), title.as_ptr());
            if !window.is_null() {
                ShowWindow(window, SW_RESTORE);
                SetForegroundWindow(window);
            }
        }
    }
}

fn main() {
    let verify_native = std::env::args_os().any(|argument| argument == "--verify-native");
    let mut context = tauri::generate_context!();
    if !verify_native {
        #[cfg(windows)]
        {
            if !single_instance::acquire() {
                let title = context
                    .config()
                    .app
                    .windows
                    .first()
                    .map(|window| window.title.clone())
                    .unwrap_or_default();
                single_instance::focus_existing(&title);
                return;
            }
        }
    }
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        // 37#4/37#8：官方 opener——「在资源管理器中显示」与 https 外链显式
        // 走系统浏览器（替代 WebView2 NewWindowRequested 的未定义行为）
        .plugin(tauri_plugin_opener::init())
        .setup(move |app| {
            let outcome = assemble(app, verify_native);
            if verify_native {
                // 50#1：verify 通道错误以 eprintln + exit(2) 交付而非上抛
                // panic（exit 101）——脚本按退出码即可区分成功/失败，不必解析
                // panic 输出；windows_subsystem 下 stderr 不可见时也无栈噪声
                if let Err(error) = outcome {
                    eprintln!("native verification failed: {error}");
                    std::process::exit(2);
                }
            }
            outcome
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
            materials::commands::verify_material_media,
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
    if verify_native {
        context.config_mut().app.windows.clear();
    }
    builder
        .run(context)
        .expect("error while running the Deep Tutor application");
}
