#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use backend::{commands, store::Store, BackendState};
use std::sync::Mutex;
use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let path = app.path().app_data_dir()?.join("deep-tutor.db");
            let store =
                Store::new(path).map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            app.manage(BackendState {
                store: Mutex::new(store),
            });
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
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Deep Tutor application");
}
