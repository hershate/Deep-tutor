#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod startup;

use backend::{
    agent, backup, commands, community, engagement, export, knowledge, learning, maintenance,
    materials, notes, planning, plugins, practice, projects, providers, reports, review, security,
    sharing, sync, web, BackendState,
};
use std::io::Write;
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
        // 106#8：已知文件夹 API 解析失败（系统策略/注册表损坏等极端环境）原先
        // `?` 上抛越过 StartupState 建立点 → builder.run().expect panic →
        // release（windows_subsystem 无控制台）下是无声闪退；转失败恢复 UI
        // 走错误页，至少给出可见错误与指引
        match (|| -> Result<(std::path::PathBuf, std::path::PathBuf, std::path::PathBuf, Option<backend::error::BackendError>), Box<dyn std::error::Error>> {
            let default_root = app.path().app_data_dir()?;
            let pointer = app
                .path()
                .app_local_data_dir()?
                .join("storage-location.json");
            let selected = startup::configured_directory(&pointer, &default_root);
            let root = selected.clone().unwrap_or_else(|_| default_root.clone());
            Ok((root, default_root, pointer, selected.err()))
        })() {
            Ok(resolved) => resolved,
            Err(error) => {
                startup::present_startup_failure(
                    app.handle(),
                    &format!("无法定位应用数据目录：{error}。请检查系统已知文件夹配置后重试。"),
                );
                return Ok(());
            }
        }
    };
    if verify_native {
        app.handle()
            .manage(security::SecurityState::for_verification(
                &data_root.join("deep-tutor.db"),
            ));
        // 127#9+133#4：verify 分支对 assemble 全程 panic 兜底——原先只包
        // initialize，其后的 ensure_ready/bootstrap/快照断言段 panic 仍以 101
        // 退出，CI 把「verify 契约失败」误读成「应用崩溃」；全程包住对齐
        // 50#1 契约（校验不过 → exit 2）。Result 路径仍上抛交调用方按 50#1
        // 转 exit(2)
        let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            startup::initialize(
                app.handle(),
                &data_root,
                &default_root,
                &pointer,
                configuration_error,
            );
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
            // 106#6：断言失败原先以 panic（退出码 101）交付，违背 50#1 声明的
            // 「verify 校验不过 → exit(2)」契约，CI 按退出码会把内部断言崩坏
            // 误读为运行崩溃；打印也改忽略错误（stdout 管道关闭不再 panic）
            if !snapshot["tasks"].is_array() || !snapshot["preferences"].is_object() {
                eprintln!("native verification failed: unexpected bootstrap snapshot shape");
                app.handle().exit(2);
            }
            let _ = writeln!(
                std::io::stdout(),
                "native verification passed: workspace={}, lastEventId={}",
                snapshot["workspaceId"],
                snapshot["lastEventId"]
            );
            app.handle().exit(0);
            Ok(())
        }));
        match outcome {
            Ok(Ok(())) => {}
            Ok(Err(error)) => return Err(error),
            Err(panic) => {
                let detail = panic
                    .downcast_ref::<&str>()
                    .map(|value| (*value).to_string())
                    .or_else(|| panic.downcast_ref::<String>().cloned())
                    .unwrap_or_else(|| "未知 panic".into());
                eprintln!("verify-native: assemble 崩溃——{detail}");
                std::process::exit(2);
            }
        }
    } else {
        startup::initialize_background(
            app.handle(),
            &data_root,
            &default_root,
            &pointer,
            configuration_error,
        );
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
    // 86#11：同名互斥体存在但 DACL 拒绝访问时 GetLastError 返回
    // ERROR_ACCESS_DENIED——原先只排除 ALREADY_EXISTS，该情形被误判
    // 「可启动」造成双开（对同一数据目录引发 SQLite 锁竞争）
    const ERROR_ACCESS_DENIED: u32 = 5;
    const SW_RESTORE: i32 = 9;

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateMutexW(
            attributes: *mut core::ffi::c_void,
            initial_owner: i32,
            name: *const u16,
        ) -> *mut core::ffi::c_void;
        fn GetLastError() -> u32;
        fn CloseHandle(handle: *mut core::ffi::c_void) -> i32;
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
    /// 106#5：Global\ 前缀使互斥体落全局命名空间——原先会话本地命名空间
    /// 下同一账号的两个交互会话（快速用户切换/本地+RDP）可双开同一数据目录。
    /// 106#3：重启子进程（DEEPTUTOR_RESTARTING 标记由 restart_application
    /// 经继承环境传入）在短窗口内重试 acquire——tauri 的 restart 先 spawn
    /// 新进程再退出旧进程，旧进程垂死期间仍持互斥体，立即认输会把
    /// 「保存并立即重启」变成「直接退出不回来」
    pub fn acquire() -> bool {
        let name = wide("Global\\DeepTutor-SingleInstance-7f3a9c2e");
        let restarting = std::env::var_os("DEEPTUTOR_RESTARTING").is_some();
        unsafe {
            for attempt in 0.. {
                let handle = CreateMutexW(std::ptr::null_mut(), 0, name.as_ptr());
                // 句柄为 NULL 仅在极端分配失败——按可启动处理，不阻塞用户
                if handle.is_null() {
                    return true;
                }
                let error = GetLastError();
                if error != ERROR_ALREADY_EXISTS && error != ERROR_ACCESS_DENIED {
                    return true;
                }
                // 133#3：重试轮的句柄先关再等——原先每轮泄漏 1 个（≤20，靠第二
                // 实例退出由 OS 兜底回收）；仅重启子进程在 2s 退避窗口内等待
                // 垂死的旧实例释放
                if restarting && attempt < 20 {
                    let _ = CloseHandle(handle);
                    std::thread::sleep(std::time::Duration::from_millis(100));
                    continue;
                }
                return false;
            }
            unreachable!()
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
    // 133#5：事件循环失败的诊断落盘点——run 失败时拿不到 app 句柄，setup
    // 期间把数据根路径存到这里供事后写 diagnostics.log
    let diagnostic_root: std::sync::Arc<std::sync::OnceLock<std::path::PathBuf>> =
        std::sync::Arc::new(std::sync::OnceLock::new());
    let root_slot = std::sync::Arc::clone(&diagnostic_root);
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        // 37#4/37#8：官方 opener——「在资源管理器中显示」与 https 外链显式
        // 走系统浏览器（替代 WebView2 NewWindowRequested 的未定义行为）
        .plugin(tauri_plugin_opener::init())
        .setup(move |app| {
            let outcome = assemble(app, verify_native);
            // 133#5：记录数据根供事件循环失败的诊断落盘
            if let Some(state) = app.try_state::<BackendState>() {
                let _ = root_slot.set(state.learning.database_path().to_path_buf());
            }
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
            // 92 号：events_since 前端零调用（38#2 起改为「事件触发即全量
            // 快照」策略），从注册表移除收敛 IPC 暴露面（同 61 号先例）；
            // 命令本体保留，测试 mock 注册面仍在使用
            commands::reveal_path,
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
            // 61 号：fetch_webpage 前端零调用（import_webpage 内部复用同一抓取
            // 链），从注册表移除收敛 IPC 面
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
            // 21#2：stream_provider_chat_with_tools 前端零调用（Agent 工具流量
            // 经 route_provider_chat 在 Rust 内部直调），从注册表移除收敛 IPC 面。
            // 138#1：stream_provider_chat 前端同样零调用（实链路是
            // route_provider_chat/get_provider_chat），同口径收敛；后端函数
            // 保留供 protocol_tests 直调
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
            // 61 号：export_usage_csv 前端零调用（37#5 由 _file 版替代），
            // 从注册表移除收敛 IPC 面
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
            // 61 号：export_practice_mistakes 前端零调用（37#5 由 _file 版
            // 替代），从注册表移除收敛 IPC 面
            practice::commands::export_practice_mistakes_file,
            practice::commands::set_practice_bookmark,
            practice::commands::set_practice_mistake_mastered,
            practice::commands::cancel_practice_answer,
            practice::commands::resume_practice_answer,
            practice::commands::abandon_practice_answer,
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
            // 138#1：get_agent_run 前端零调用（会话读取走 get_agent_session），
            // 从注册表移除收敛 IPC 面；后端函数保留（resume 内部使用+测试直调）
            agent::commands::cancel_agent_run,
            agent::commands::resume_agent_run,
            agent::commands::delete_agent_session,
            agent::commands::approve_agent_request,
            agent::commands::cancel_agent_approval,
        ]));
    if verify_native {
        context.config_mut().app.windows.clear();
    }
    if let Err(error) = builder.run(context) {
        // 133#5：原先 .expect panic 只进 stderr——windows_subsystem 下无控制台，
        // 用户双击后窗口不出现且零反馈；写 diagnostics.log（数据根已建立时）
        // 并以退出码 1 结束（verify 通道 stderr 被 CI 捕获）
        eprintln!("error while running the Deep Tutor application: {error}");
        if let Some(account_path) = diagnostic_root.get() {
            backend::store::append_diagnostic(account_path, &format!("事件循环失败：{error}"));
        }
        std::process::exit(1);
    }
}
